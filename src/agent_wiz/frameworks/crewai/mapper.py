import argparse
import ast
import json
import os
from pathlib import Path
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum
import yaml


def get_potential_fqn(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        base = get_potential_fqn(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None

def _safe_load_yaml(filepath: str) -> Optional[Dict]:
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Warning: Error parsing YAML file {filepath}: {e}", file=sys.stderr)
    except IOError as e:
        print(f"Warning: Error reading YAML file {filepath}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Unexpected error loading YAML {filepath}: {e}", file=sys.stderr)
    return None

def _stringify_ast_node(node: ast.AST) -> str:
    if isinstance(node, ast.Constant): return repr(node.value)
    elif isinstance(node, ast.Name): return node.id
    elif isinstance(node, ast.Attribute): return get_potential_fqn(node) or f"<Attribute {node.attr}>"
    elif isinstance(node, ast.Subscript):
        base = _stringify_ast_node(node.value)
        slc = _stringify_ast_node(node.slice)
        return f"{base}[{slc}]"
    elif isinstance(node, ast.Index): # support for Python < 3.9
         return _stringify_ast_node(node.value)
    elif isinstance(node, ast.Tuple):
         return f"({', '.join(_stringify_ast_node(elt) for elt in node.elts)})"
    elif isinstance(node, ast.List):
         return f"[{', '.join(_stringify_ast_node(elt) for elt in node.elts)}]"
    elif isinstance(node, ast.Dict):
         pairs = [f"{_stringify_ast_node(k)}: {_stringify_ast_node(v)}" for k, v in zip(node.keys, node.values)]
         return f"{{{', '.join(pairs)}}}"
    elif isinstance(node, ast.Call):
         func = _stringify_ast_node(node.func)
         args = [_stringify_ast_node(arg) for arg in node.args]
         kwargs = [f"{kw.arg}={_stringify_ast_node(kw.value)}" for kw in node.keywords if kw.arg]
         return f"{func}({', '.join(args + kwargs)})"
    else:
        try: return ast.dump(node)
        except Exception: return f"<AST:{type(node).__name__}>"


class CrewProcess(str, Enum):
    SEQUENTIAL = "sequential"
    HIERARCHICAL = "hierarchical"
    UNKNOWN = "unknown"


NODE_TYPE_MAP = {
    "agent": "Agent",
    "task": "Generic",
    "tool": "ToolExecutor",
    "custom_tool": "ToolExecutor",
    "start": "Special",
    "end": "Special",
}


class CrewAIStructureExtractor(ast.NodeVisitor):
    def __init__(self):
        self.agents_info: Dict[str, Dict[str, Any]] = {}
        self.tasks_info: Dict[str, Dict[str, Any]] = {}
        self.crews_info: Dict[str, Dict[str, Any]] = {}
        self.tools_info: Dict[str, Dict[str, Any]] = {}
        self.task_lists: Dict[str, Dict[str, Any]] = {}
        self.agent_lists: Dict[str, Dict[str, Any]] = {}
        # --- New: Track self.attribute assignments for tool resolution ---
        self.tool_instance_map: Dict[str, Dict[str, ast.AST]] = defaultdict(dict) # class_name -> {attr_name: value_node}

        self.imports: Dict[str, str] = {}
        self.tool_imports: Set[str] = set()
        self.current_filepath: Optional[str] = None
        self.current_class_name: Optional[str] = None
        self.current_crew_base_info: Optional[Dict] = None

        self.crewai_fqns = {
            "Agent": "crewai.agent.Agent",
            "Task": "crewai.task.Task",
            "Crew": "crewai.crew.Crew",
            "Process": "crewai.process.Process",
            "CrewBase": "crewai.project.crew_base.CrewBase",
            "agent": "crewai.project.decorators.agent",
            "task": "crewai.project.decorators.task",
            "crew": "crewai.project.decorators.crew",
            "tool": "crewai.tools.tool.tool",
            "BaseTool": "crewai_tools.tools.base_tool.BaseTool"
        }

        self.output_nodes: List[Dict[str, Any]] = []
        self.output_edges: List[Dict[str, Any]] = []
        self.output_node_names: Set[str] = set()


    def _get_location_dict(self, node: ast.AST) -> Optional[Dict[str, Any]]:
        if not self.current_filepath or not hasattr(node, 'lineno'):
             return None
        return {
            "file": self.current_filepath,
            "line": node.lineno,
            "col": node.col_offset,
            "end_line": getattr(node, 'end_lineno', node.lineno),
            "end_col": getattr(node, 'end_col_offset', -1),
        }

    def _add_output_node(self, name: str, function_name: Optional[str], docstring: Optional[str],
                         node_type: str, source_location: Optional[Dict], metadata: Optional[Dict]):
        if name not in self.output_node_names:
            node_data = {
                "name": name,
                "function_name": function_name,
                "docstring": docstring,
                "node_type": node_type,
                "source_location": source_location,
                "metadata": metadata or {}
            }
            self.output_nodes.append(node_data)
            self.output_node_names.add(name)

    def _add_output_edge(self, source: str, target: str, condition: Dict, metadata: Optional[Dict]):
        if source in self.output_node_names and target in self.output_node_names:
            # TODO: Add more robust duplicate check if needed
            self.output_edges.append({
                "source": source,
                "target": target,
                "condition": condition,
                "metadata": metadata or {}
            })

    def _resolve_fqn_from_node(self, node: ast.AST) -> Optional[str]:
        potential_name = get_potential_fqn(node)
        if potential_name:
             if potential_name in self.imports:
                  return self.imports[potential_name]
             parts = potential_name.split('.')
             if len(parts) > 1:
                  base_alias = parts[0]
                  if base_alias in self.imports:
                       if self.imports[base_alias] == base_alias:
                            return potential_name
                       else:
                            return f"{self.imports[base_alias]}.{'.'.join(parts[1:])}"
             return potential_name
        return None

    def _get_string_value(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        elif isinstance(node, ast.JoinedStr):
             try:
                  return "".join(str(v.value) for v in node.values if isinstance(v, ast.Constant))
             except: return "<f-string>"
        return None

    def _get_list_of_identifiers(self, node: ast.AST) -> Optional[List[str]]:
        if isinstance(node, ast.List):
            elements = []
            for elt in node.elts:
                name = get_potential_fqn(elt)
                if name:
                    elements.append(name)
                elif isinstance(elt, ast.Call):
                     call_name = get_potential_fqn(elt.func)
                     if call_name:
                          elements.append(call_name)
                else:
                    elements.append(_stringify_ast_node(elt))
            return elements
        return None

    def _get_call_kwargs(self, node: ast.Call) -> Dict[str, Any]:
        kwargs = {}
        for keyword in node.keywords:
            arg_name = keyword.arg
            if arg_name:
                value_node = keyword.value
                if isinstance(value_node, ast.Constant):
                    kwargs[arg_name] = value_node.value
                elif isinstance(value_node, (ast.Name, ast.Attribute)):
                    resolved_name = get_potential_fqn(value_node)
                    kwargs[arg_name] = resolved_name if resolved_name else _stringify_ast_node(value_node)
                elif isinstance(value_node, ast.List):
                    kwargs[arg_name] = self._get_list_of_identifiers(value_node) or _stringify_ast_node(value_node)
                else:
                    kwargs[arg_name] = _stringify_ast_node(value_node)
        return kwargs

    def _parse_config_arg(self, node: ast.AST, base_dir: str) -> Tuple[Optional[str], Optional[Dict]]:
        config_ref_str = _stringify_ast_node(node)
        config_data = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            config_path = node.value
            abs_path = os.path.join(base_dir, config_path) if not os.path.isabs(config_path) else config_path
            config_data = _safe_load_yaml(abs_path)
        return config_ref_str, config_data


    def _find_return_call(self, body_nodes: List[ast.AST], target_class_fqn: str) -> Optional[ast.Call]:
        for stmt in body_nodes:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
                call_node = stmt.value
                func_fqn = self._resolve_fqn_from_node(call_node.func)
                func_name_simple = get_potential_fqn(call_node.func)
                if func_fqn == target_class_fqn or func_name_simple == target_class_fqn or \
                   (isinstance(call_node.func, ast.Name) and target_class_fqn.endswith(f".{call_node.func.id}")):
                    return call_node
            elif isinstance(stmt, (ast.If, ast.Try)): # Recurse into simple blocks
                call = self._find_return_call(stmt.body, target_class_fqn)
                if call: return call
                if hasattr(stmt, 'orelse'):
                    call = self._find_return_call(stmt.orelse, target_class_fqn)
                    if call: return call
                if hasattr(stmt, 'finalbody'):
                     call = self._find_return_call(stmt.finalbody, target_class_fqn)
                     if call: return call
            # TODO: Could add recursion for If/Try blocks if needed (more complex)
        return None


    def visit(self, node: ast.AST, filepath: Optional[str] = None):
        original_path = self.current_filepath
        if filepath:
            self.current_filepath = filepath
        super().visit(node)
        self.current_filepath = original_path

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
             module_name = alias.name
             local_name = alias.asname or module_name
             self.imports[local_name] = module_name
             if module_name == 'crewai':
                  self.crewai_fqns['Agent'] = 'crewai.Agent'
                  self.crewai_fqns['Task'] = 'crewai.Task'
                  self.crewai_fqns['Crew'] = 'crewai.Crew'
                  self.crewai_fqns['Process'] = 'crewai.Process'
                  self.crewai_fqns['CrewBase'] = 'crewai.CrewBase'
                  self.crewai_fqns['agent'] = 'crewai.agent'
                  self.crewai_fqns['task'] = 'crewai.task'
                  self.crewai_fqns['crew'] = 'crewai.crew'
             if module_name == 'crewai_tools':
                  self.crewai_fqns['BaseTool'] = 'crewai_tools.BaseTool'
                  self.crewai_fqns['tool'] = 'crewai_tools.tool'

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            base_module = node.module
            for alias in node.names:
                imported_name = alias.name
                local_name = alias.asname or imported_name
                full_path = f"{base_module}.{imported_name}"
                self.imports[local_name] = full_path

                for key, default_fqn in self.crewai_fqns.items():
                    if full_path == default_fqn:
                        self.crewai_fqns[key] = local_name
                        break

                if base_module == 'crewai_tools' or base_module.startswith('crewai_tools.'):
                    self.tool_imports.add(imported_name)
                    if alias.asname:
                         self.tool_imports.add(local_name)

    def visit_ClassDef(self, node: ast.ClassDef):
         original_class_name = self.current_class_name
         original_crew_base_info = self.current_crew_base_info
         is_crew_base = False
         class_location = self._get_location_dict(node)
         class_docstring = ast.get_docstring(node)

         cb_decorator_name = self.crewai_fqns.get('CrewBase', 'crewai.project.CrewBase')
         for decorator in node.decorator_list:
              dec_name = get_potential_fqn(decorator)
              if dec_name == 'CrewBase' or self._resolve_fqn_from_node(decorator) == cb_decorator_name:
                   is_crew_base = True
                   break

         if is_crew_base:
              self.current_class_name = node.name
              self.current_crew_base_info = {"name": node.name, "agents": [], "tasks": [], "crew_method": None, "location": class_location, "docstring": class_docstring}

         bt_class_name = self.crewai_fqns.get('BaseTool', 'crewai_tools.BaseTool')
         is_custom_tool_class = False
         for base in node.bases:
             resolved_base_fqn = self._resolve_fqn_from_node(base)
             if resolved_base_fqn == bt_class_name or (isinstance(base, ast.Name) and base.id == 'BaseTool'):
                 is_custom_tool_class = True
                 break

         if is_custom_tool_class:
             tool_name = node.name
             tool_description = class_docstring or f"Custom tool class: {tool_name}"
             self.tools_info[tool_name] = {
                 "name": tool_name,
                 "function_name": tool_name,
                 "docstring": class_docstring,
                 "is_custom": True,
                 "description": tool_description,
                 "var_name": None,
                 "location": class_location,
                 "type": "class"
             }

         self.generic_visit(node)

         # --- Populate tool_instance_map after visiting class body (including __init__) ---
         if self.current_class_name == node.name: # Check if we're back from visiting this class
            init_method = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == '__init__'), None)
            if init_method:
                for stmt in init_method.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                        target = stmt.targets[0]
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                            attr_name = target.attr
                            # Check if the assigned value is a tool instance we care about
                            value_node = stmt.value
                            tool_class_name = None
                            if isinstance(value_node, ast.Call):
                                tool_class_name = get_potential_fqn(value_node.func)

                            # Only store if it looks like a known tool or custom tool
                            if tool_class_name and (tool_class_name in self.tool_imports or tool_class_name in self.tools_info):
                                self.tool_instance_map[node.name][attr_name] = value_node


         self.current_class_name = original_class_name
         self.current_crew_base_info = original_crew_base_info


    def visit_FunctionDef(self, node: ast.FunctionDef):
        func_name = node.name
        is_crewai_decorated = False
        base_dir = os.path.dirname(self.current_filepath) if self.current_filepath else "."
        func_location = self._get_location_dict(node)
        func_docstring = ast.get_docstring(node)

        agent_decorator = self.crewai_fqns.get('agent')
        task_decorator = self.crewai_fqns.get('task')
        crew_decorator = self.crewai_fqns.get('crew')
        tool_decorator = self.crewai_fqns.get('tool')

        for decorator in node.decorator_list:
            decorator_fqn = self._resolve_fqn_from_node(decorator)
            decorator_name = get_potential_fqn(decorator)

            if self.current_crew_base_info and (decorator_fqn == agent_decorator or decorator_name == 'agent'):
                 is_crewai_decorated = True
                 agent_call = self._find_return_call(node.body, self.crewai_fqns['Agent'])
                 if agent_call:
                      agent_id = func_name
                      kwargs = self._get_call_kwargs(agent_call)
                      config_ref, config_data = (None, None)
                      config_kw_node = next((kw.value for kw in agent_call.keywords if kw.arg == 'config'), None)
                      if config_kw_node:
                           config_ref, config_data = self._parse_config_arg(config_kw_node, base_dir)

                      tool_refs = kwargs.get("tools", [])
                      self.agents_info[agent_id] = {
                           "name": agent_id,
                           "function_name": agent_id,
                           "docstring": func_docstring,
                           "location": func_location,
                           "config_ref": config_ref,
                           "config_data": config_data,
                           "tools_vars": tool_refs if isinstance(tool_refs, list) else [],
                           "metadata_kwargs": kwargs,
                           "is_decorator": True,
                           "class_context": self.current_class_name
                      }
                      self.current_crew_base_info["agents"].append(agent_id)
                 break

            elif self.current_crew_base_info and (decorator_fqn == task_decorator or decorator_name == 'task'):
                 is_crewai_decorated = True
                 task_call = self._find_return_call(node.body, self.crewai_fqns['Task'])
                 if task_call:
                      task_id = func_name
                      kwargs = self._get_call_kwargs(task_call)
                      config_ref, config_data = (None, None)
                      config_kw_node = next((kw.value for kw in task_call.keywords if kw.arg == 'config'), None)
                      if config_kw_node:
                           config_ref, config_data = self._parse_config_arg(config_kw_node, base_dir)

                      agent_var = None # Explicitly set to None for decorator tasks initially
                      agent_kw_node = next((kw.value for kw in task_call.keywords if kw.arg == 'agent'), None)
                      if agent_kw_node: # If agent= IS present, parse it
                         agent_ref_node_str = _stringify_ast_node(agent_kw_node)
                         if isinstance(agent_ref_node_str, str):
                            agent_var = agent_ref_node_str.removesuffix('()')
                            if agent_var.startswith('self.'):
                                agent_var = agent_var[5:]

                      output_ref = kwargs.get("output_json") or kwargs.get("output_pydantic") or kwargs.get("output_file")
                      context_vars = kwargs.get("context", [])
                      # --- Read dependencies from YAML if config was loaded ---
                      dependencies = []
                      if config_data and isinstance(config_data.get('dependencies'), list):
                          dependencies = config_data['dependencies']
                      # --- Also check kwargs for dependencies (less common) ---
                      elif isinstance(kwargs.get('dependencies'), list):
                           dependencies = kwargs['dependencies']

                      self.tasks_info[task_id] = {
                           "name": task_id,
                           "function_name": task_id,
                           "docstring": func_docstring,
                           "location": func_location,
                           "config_ref": config_ref,
                           "config_data": config_data,
                           "agent_var": agent_var,
                           "description": kwargs.get("description", config_data.get("description", task_id) if config_data else task_id),
                           "context_vars": context_vars if isinstance(context_vars, list) else [],
                           "output_ref": output_ref,
                           "metadata_kwargs": kwargs,
                           "dependencies": dependencies, # Store dependencies
                           "is_decorator": True,
                           "class_context": self.current_class_name
                      }
                      self.current_crew_base_info["tasks"].append(task_id)
                 break

            elif self.current_crew_base_info and (decorator_fqn == crew_decorator or decorator_name == 'crew'):
                is_crewai_decorated = True
                crew_call = self._find_return_call(node.body, self.crewai_fqns['Crew'])
                if crew_call:
                     crew_id = self.current_class_name
                     kwargs = self._get_call_kwargs(crew_call)

                     agents_list_ref = kwargs.get("agents")
                     if agents_list_ref == "self.agents":
                          agents_list_ref = self.current_crew_base_info["agents"]

                     tasks_list_ref = kwargs.get("tasks")
                     if tasks_list_ref == "self.tasks":
                          tasks_list_ref = self.current_crew_base_info["tasks"]

                     process_type_str = kwargs.get("process", "sequential")
                     process_type = self._parse_process_type(process_type_str)

                     self.crews_info[crew_id] = {
                          "name": crew_id,
                          "location": self.current_crew_base_info.get("location"),
                          "definition_location": func_location,
                          "agents_list_ref": agents_list_ref,
                          "tasks_list_ref": tasks_list_ref,
                          "process_type": process_type,
                          "metadata_kwargs": kwargs,
                          "is_decorator": True,
                          "class_context": self.current_class_name
                     }
                     self.current_crew_base_info["crew_method"] = func_name
                break

            elif decorator_fqn == tool_decorator or decorator_name == 'tool' or (isinstance(decorator, ast.Call) and get_potential_fqn(decorator.func) == 'tool'):
                is_crewai_decorated = True
                tool_name = func_name
                tool_description = func_docstring or f"Custom tool function: {tool_name}"
                self.tools_info[tool_name] = {
                     "name": tool_name,
                     "function_name": tool_name,
                     "docstring": func_docstring,
                     "location": func_location,
                     "is_custom": True,
                     "description": tool_description,
                     "var_name": None,
                     "type": "function"
                }
                break

        if not is_crewai_decorated:
            self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        assign_location = self._get_location_dict(node)
        if len(node.targets) == 1 :
            target_node = node.targets[0]

            # --- Handle self.attribute assignment ---
            if isinstance(target_node, ast.Attribute) and isinstance(target_node.value, ast.Name) and target_node.value.id == 'self' and self.current_class_name:
                attr_name = target_node.attr
                value_node = node.value
                tool_class_name = None
                if isinstance(value_node, ast.Call):
                    tool_class_name = get_potential_fqn(value_node.func)

                if tool_class_name and (tool_class_name in self.tool_imports or tool_class_name in self.tools_info):
                     self.tool_instance_map[self.current_class_name][attr_name] = value_node
            # --- End self.attribute handling ---

            elif isinstance(target_node, ast.Name): # Handle regular variable assignment
                target_var = target_node.id
                value_node = node.value
                base_dir = os.path.dirname(self.current_filepath) if self.current_filepath else "."

                if isinstance(value_node, ast.List):
                    ids = self._get_list_of_identifiers(value_node)
                    if ids:
                        list_info = {"tasks": ids, "agents": ids, "location": assign_location}
                        self.task_lists[target_var] = list_info
                        self.agent_lists[target_var] = list_info

                elif isinstance(value_node, ast.Call):
                    call_func = value_node.func
                    kwargs = self._get_call_kwargs(value_node)
                    func_fqn = self._resolve_fqn_from_node(call_func)
                    func_name_simple = get_potential_fqn(call_func)

                    agent_fqn = self.crewai_fqns['Agent']
                    task_fqn = self.crewai_fqns['Task']
                    crew_fqn = self.crewai_fqns['Crew']

                    if func_fqn == agent_fqn or func_name_simple == 'Agent':
                        config_ref, config_data = (None, None)
                        config_kw_node = next((kw.value for kw in value_node.keywords if kw.arg == 'config'), None)
                        if config_kw_node: config_ref, config_data = self._parse_config_arg(config_kw_node, base_dir)
                        tool_refs = kwargs.get("tools", [])
                        self.agents_info[target_var] = {
                            "name": target_var,
                            "function_name": target_var,
                            "docstring": None,
                            "location": assign_location,
                            "config_ref": config_ref, "config_data": config_data,
                            "tools_vars": tool_refs if isinstance(tool_refs, list) else [],
                            "metadata_kwargs": kwargs, "is_decorator": False,
                            "class_context": self.current_class_name
                        }

                    elif func_fqn == task_fqn or func_name_simple == 'Task':
                        config_ref, config_data = (None, None)
                        config_kw_node = next((kw.value for kw in value_node.keywords if kw.arg == 'config'), None)
                        if config_kw_node: config_ref, config_data = self._parse_config_arg(config_kw_node, base_dir)

                        agent_var_raw = kwargs.get("agent")
                        agent_var = agent_var_raw if isinstance(agent_var_raw, str) else None

                        output_ref = kwargs.get("output_json") or kwargs.get("output_pydantic") or kwargs.get("output_file")
                        context_vars = kwargs.get("context", [])
                        dependencies = kwargs.get("dependencies", []) # Check kwargs too

                        self.tasks_info[target_var] = {
                            "name": target_var,
                            "function_name": target_var,
                            "docstring": None,
                            "location": assign_location,
                            "config_ref": config_ref, "config_data": config_data,
                            "agent_var": agent_var,
                            "description": kwargs.get("description", target_var),
                            "context_vars": context_vars if isinstance(context_vars, list) else [],
                            "output_ref": output_ref, "metadata_kwargs": kwargs,
                            "dependencies": dependencies if isinstance(dependencies, list) else [],
                            "is_decorator": False, "class_context": self.current_class_name
                        }

                    elif func_fqn == crew_fqn or func_name_simple == 'Crew':
                        tasks_list_ref = kwargs.get("tasks")
                        agents_list_ref = kwargs.get("agents")
                        process_type_str = kwargs.get("process", "sequential")
                        process_type = self._parse_process_type(process_type_str)
                        self.crews_info[target_var] = {
                            "name": target_var,
                            "location": assign_location,
                            "definition_location": assign_location,
                            "agents_list_ref": agents_list_ref,
                            "tasks_list_ref": tasks_list_ref, "process_type": process_type,
                            "metadata_kwargs": kwargs, "is_decorator": False,
                            "class_context": self.current_class_name
                        }

                    else:
                         tool_class_name = get_potential_fqn(call_func)
                         if tool_class_name and tool_class_name in self.tool_imports:
                              if tool_class_name in self.tools_info:
                                   self.tools_info[tool_class_name]["var_name"] = target_var
                                   self.tools_info[tool_class_name]["assign_location"] = assign_location
                                   self.tools_info[target_var] = self.tools_info[tool_class_name]
                              else:
                                   self.tools_info[target_var] = {
                                        "name": target_var,
                                        "function_name": tool_class_name,
                                        "docstring": None,
                                        "location": assign_location,
                                        "is_custom": False,
                                        "description": f"Predefined tool instance: {tool_class_name}",
                                        "var_name": target_var,
                                        "tool_class": tool_class_name,
                                        "type": "instance"
                                   }


        self.generic_visit(node)

    def _parse_process_type(self, process_ref: Any) -> CrewProcess:
        proc_seq_fqn = self.crewai_fqns['Process'] + ".sequential"
        proc_hier_fqn = self.crewai_fqns['Process'] + ".hierarchical"
        process_ref_str = str(process_ref)

        if process_ref_str == proc_seq_fqn or process_ref_str.endswith('.sequential') or process_ref_str == 'sequential':
            return CrewProcess.SEQUENTIAL
        elif process_ref_str == proc_hier_fqn or process_ref_str.endswith('.hierarchical') or process_ref_str == 'hierarchical':
            return CrewProcess.HIERARCHICAL
        return CrewProcess.UNKNOWN


    def finalize_graph(self):
        print("Finalizing graph...")

        self._add_output_node(name="Start", function_name=None, docstring=None,
                             node_type=NODE_TYPE_MAP["start"], source_location=None, metadata={})
        self._add_output_node(name="End", function_name=None, docstring=None,
                             node_type=NODE_TYPE_MAP["end"], source_location=None, metadata={})

        for agent_id, agent_data in self.agents_info.items():
             metadata = agent_data.get("metadata_kwargs", {})
             if agent_data.get("config_data"):
                  metadata['role'] = agent_data["config_data"].get('role', metadata.get('role'))
                  metadata['goal'] = agent_data["config_data"].get('goal', metadata.get('goal'))
                  metadata['backstory'] = agent_data["config_data"].get('backstory', metadata.get('backstory'))
             metadata['config_ref'] = agent_data.get('config_ref')
             metadata['config_resolved'] = bool(agent_data.get("config_data"))
             metadata['tools'] = agent_data.get('tools_vars')
             metadata['is_decorator'] = agent_data.get('is_decorator')
             metadata['class_context'] = agent_data.get('class_context')

             self._add_output_node(name=agent_id,
                                  function_name=agent_data.get("function_name"),
                                  docstring=agent_data.get("docstring"),
                                  node_type=NODE_TYPE_MAP["agent"],
                                  source_location=agent_data.get("location"),
                                  metadata=metadata)

        for task_id, task_data in self.tasks_info.items():
             metadata = task_data.get("metadata_kwargs", {})
             if task_data.get("config_data"):
                  metadata['description'] = task_data["config_data"].get('description', metadata.get('description'))
             metadata['config_ref'] = task_data.get('config_ref')
             metadata['config_resolved'] = bool(task_data.get("config_data"))
             metadata['agent'] = task_data.get('agent_var') # Keep the potentially null agent_var here
             metadata['context'] = task_data.get('context_vars')
             metadata['output_ref'] = task_data.get('output_ref')
             metadata['is_decorator'] = task_data.get('is_decorator')
             metadata['class_context'] = task_data.get('class_context')
             metadata['dependencies'] = task_data.get('dependencies') # Add dependencies to metadata
             node_description = metadata.get('description') or task_data.get('description')

             self._add_output_node(name=task_id,
                                  function_name=task_data.get("function_name"),
                                  docstring=task_data.get("docstring"),
                                  node_type=NODE_TYPE_MAP["task"],
                                  source_location=task_data.get("location"),
                                  metadata=metadata)

        tool_node_map = {}
        final_tool_nodes = {}

        # --- Pass 1: Process explicitly defined/instantiated tools ---
        for tool_ref, tool_data in self.tools_info.items():
             node_name = tool_data.get("var_name") or tool_data["name"]
             if node_name not in final_tool_nodes:
                  metadata = {
                       "description": tool_data["description"],
                       "is_custom": tool_data["is_custom"],
                       "definition_type": tool_data.get("type"),
                       "tool_reference": tool_data["name"]
                  }
                  final_tool_nodes[node_name] = {
                      "name": node_name,
                      "function_name": tool_data["function_name"],
                      "docstring": tool_data.get("docstring"),
                      "node_type": NODE_TYPE_MAP["tool"],
                      "source_location": tool_data.get("location") or tool_data.get("assign_location"),
                      "metadata": metadata
                  }
             # Map original name and var_name (if exists) to the final node name
             tool_node_map[tool_data["name"]] = node_name
             if tool_data.get("var_name"):
                 tool_node_map[tool_data["var_name"]] = node_name

        # --- Pass 2: Resolve self. references and create implicit nodes ---
        all_tool_refs_found = set()
        for agent_data in self.agents_info.values():
            all_tool_refs_found.update(agent_data.get('tools_vars', []))

        for tool_ref in all_tool_refs_found:
            if tool_ref in tool_node_map: continue # Already handled

            resolved_node_name = None
            # Try resolving self.attribute
            if isinstance(tool_ref, str) and tool_ref.startswith('self.'):
                class_context = agent_data.get('class_context') # Assumes agent_data is in scope (needs adjustment if run outside agent loop)
                attr_name = tool_ref[5:]
                if class_context and attr_name in self.tool_instance_map.get(class_context, {}):
                    value_node = self.tool_instance_map[class_context][attr_name]
                    # Find the original tool class/function name from the assignment
                    tool_class_name = None
                    if isinstance(value_node, ast.Call):
                        tool_class_name = get_potential_fqn(value_node.func)
                    elif isinstance(value_node, ast.Name): # Assigned from another var/tool func
                        tool_class_name = value_node.id
                    # Map this class/func name back to a node name if possible
                    if tool_class_name:
                        resolved_node_name = tool_node_map.get(tool_class_name)

            # If not resolved via self. or already mapped, check if it's an imported tool name
            if not resolved_node_name and tool_ref in self.tool_imports and tool_ref not in tool_node_map:
                 resolved_node_name = tool_ref # Use the import name directly

            # If still not resolved, create implicit node
            if not resolved_node_name:
                 node_name = tool_ref
                 if node_name not in final_tool_nodes:
                    metadata = {
                        "description": f"Implicitly referenced tool: {tool_ref}",
                        "is_custom": False,
                        "definition_type": "reference",
                        "tool_reference": tool_ref
                    }
                    final_tool_nodes[node_name] = {
                        "name": node_name, "function_name": None, "docstring": None,
                        "node_type": NODE_TYPE_MAP["tool"], "source_location": None,
                        "metadata": metadata
                    }
                 resolved_node_name = node_name

            # Map the original reference (e.g., 'self.whisper_tool') to the final node name
            if resolved_node_name:
                tool_node_map[tool_ref] = resolved_node_name

        # Add all collected tool nodes
        for node_name, node_creation_data in final_tool_nodes.items():
             self._add_output_node(**node_creation_data)


        # --- Create Edges ---
        # 5. Agent -> Task Assignment (Handles decorator inference)
        for task_id, task_data in self.tasks_info.items():
            agent_node_name = task_data.get("agent_var") # Explicitly assigned agent

            # If decorator task and no agent explicitly assigned in Task(), infer by name matching
            if task_data.get("is_decorator") and not agent_node_name:
                task_name = task_data["name"]
                class_context = task_data.get("class_context")
                # Check if an agent with the same name exists in the same class context
                if class_context and task_name in self.agents_info and self.agents_info[task_name].get("class_context") == class_context:
                    agent_node_name = task_name # Inferred agent name

            if agent_node_name and agent_node_name in self.agents_info:
                 condition = {"type": "assignment"}
                 metadata = {"definition_location": task_data.get("location")}
                 self._add_output_edge(agent_node_name, task_id, condition, metadata)
            # else:
            #      print(f"Debug: Could not link agent to task '{task_id}'. Explicit: '{task_data.get('agent_var')}', Inferred: '{agent_node_name}'")


        # 6. Agent -> Tool Usage (Uses resolved tool_node_map)
        for agent_id, agent_data in self.agents_info.items():
             tool_refs = agent_data.get("tools_vars", [])
             agent_def_location = agent_data.get("location")
             for tool_ref in tool_refs:
                 tool_node_name = tool_node_map.get(tool_ref) # Lookup resolved name
                 if tool_node_name:
                      condition = {"type": "tool_usage"}
                      metadata = {"definition_location": agent_def_location}
                      self._add_output_edge(agent_id, tool_node_name, condition, metadata)
                 # else:
                 #      print(f"Debug: Could not find tool node for reference '{tool_ref}' used by agent '{agent_id}'")


        # 7. Crew Execution Flow (Task sequencing)
        processed_tasks_in_crews = set()
        for crew_id, crew_data in self.crews_info.items():
            crew_def_location = crew_data.get("definition_location")
            task_sequence_refs = crew_data.get("tasks_list_ref")
            actual_task_sequence = []

            if isinstance(task_sequence_refs, list):
                 actual_task_sequence = task_sequence_refs
            elif isinstance(task_sequence_refs, str):
                 if task_sequence_refs in self.task_lists:
                      actual_task_sequence = self.task_lists[task_sequence_refs].get("tasks", [])
                      if not crew_def_location: crew_def_location = self.task_lists[task_sequence_refs].get("location")

            if not actual_task_sequence:
                 print(f"Warning: No valid task sequence found for crew '{crew_id}'.", file=sys.stderr)
                 continue

            valid_task_node_names = [t for t in actual_task_sequence if t in self.tasks_info]
            processed_tasks_in_crews.update(valid_task_node_names)

            if not valid_task_node_names: continue

            process_type = crew_data.get("process_type", CrewProcess.SEQUENTIAL)
            crew_metadata = {"crew_name": crew_id, "process_type": process_type.value, "definition_location": crew_def_location}

            if process_type == CrewProcess.SEQUENTIAL:
                start_condition = {"type": "entry_point"}
                self._add_output_edge("Start", valid_task_node_names[0], start_condition, crew_metadata)
                seq_condition = {"type": "static"}
                for i in range(len(valid_task_node_names) - 1):
                     self._add_output_edge(valid_task_node_names[i], valid_task_node_names[i+1], seq_condition, crew_metadata)
                end_condition = {"type": "finish_point"}
                self._add_output_edge(valid_task_node_names[-1], "End", end_condition, crew_metadata)

            elif process_type == CrewProcess.HIERARCHICAL:
                 start_condition = {"type": "entry_point", "detail": "hierarchical"}
                 end_condition = {"type": "finish_point", "detail": "hierarchical"}
                 for task_node_name in valid_task_node_names:
                     self._add_output_edge("Start", task_node_name, start_condition, crew_metadata)
                     self._add_output_edge(task_node_name, "End", end_condition, crew_metadata)
                 # TODO: Optionally add all-to-all task edges if needed for visualization

        # 8. Task -> Task (Context & Dependencies)
        for task_id, task_data in self.tasks_info.items():
             task_def_location = task_data.get("location")
             # Context Edges
             context_vars = task_data.get("context_vars", [])
             if isinstance(context_vars, list):
                 for ctx_task_var in context_vars:
                     if ctx_task_var in self.tasks_info:
                         condition = {"type": "context_dependency"}
                         metadata = {"definition_location": task_def_location}
                         self._add_output_edge(ctx_task_var, task_id, condition, metadata)
            # Dependency Edges
             dependencies = task_data.get("dependencies", [])
             if isinstance(dependencies, list):
                 for dep_task_name in dependencies:
                     if dep_task_name in self.tasks_info:
                         condition = {"type": "dependency"} # Use a specific type
                         metadata = {"definition_location": task_def_location} # Dependency defined in target task
                         # Edge direction: Dependency -> Dependent Task
                         self._add_output_edge(dep_task_name, task_id, condition, metadata)


    def get_graph_data(self) -> Dict[str, List[Dict[str, Any]]]:
        return {"nodes": self.output_nodes, "edges": self.output_edges}

def extract_crewai_graph(directory_path: str, output_file: str):
    extractor = CrewAIStructureExtractor()
    print(f"Starting CrewAI structure extraction in: {directory_path}")

    if not os.path.isdir(directory_path):
         print(f"Error: Provided path '{directory_path}' is not a valid directory.", file=sys.stderr)
         sys.exit(1)

    parsed_files = 0
    for root, _, files in os.walk(directory_path):
        path_parts = Path(root).parts
        if any(part.startswith('.') or part in ['venv', 'env', '__pycache__', 'node_modules', '.git'] for part in path_parts):
            continue

        for filename in files:
            if filename.endswith(".py"):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding='utf-8') as f:
                        content = f.read()
                    tree = ast.parse(content, filename=filepath)
                    extractor.visit(tree, filepath=filepath)
                    parsed_files += 1
                except SyntaxError as e:
                    print(f"Warning: Skipping file {filepath} due to SyntaxError: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"Warning: Skipping file {filepath} due to error: {e}", file=sys.stderr)

    print(f"AST parsing complete ({parsed_files} files processed). Finalizing graph structure...")
    extractor.finalize_graph()
    graph_structure = extractor.get_graph_data()
    print(f"Extraction finished. Found {len(graph_structure['nodes'])} nodes and {len(graph_structure['edges'])} edges.")


    if graph_structure["nodes"] or graph_structure["edges"]:
        try:
            graph_structure["nodes"].sort(key=lambda x: (x.get("node_type", ""), x.get("name", "")))
            graph_structure["edges"].sort(key=lambda x: (x.get("source", ""), x.get("target", "")))

            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding='utf-8') as f:
                json.dump(graph_structure, f, indent=2)
            print(f"Successfully wrote graph data to {output_path}")
        except IOError as e:
            print(f"Error writing output file {output_path}: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
             print(f"An unexpected error occurred during JSON serialization or path creation: {e}", file=sys.stderr)
             sys.exit(1)
    else:
        print("No graph structure extracted. Output file not written.")
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding='utf-8') as f:
                json.dump({"nodes": [], "edges": []}, f, indent=2)
            print(f"Empty graph structure written to {output_path}")
        except Exception as e:
            print(f"Error writing empty JSON output to {output_path}: {e}")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract CrewAI structures from Python code into a graph JSON.")
    parser.add_argument("directory", type=str, nargs='?', default=".", help="Directory containing the Python code (default: current directory).")
    parser.add_argument("-o", "--output", type=str, default="crewai_graph_output.json", help="Output JSON filename (default: crewai_graph_output.json).")
    args = parser.parse_args()

    target_dir = args.directory
    output_file = args.output

    extract_crewai_graph(target_dir, output_file)