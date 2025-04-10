import ast
import json
import os
import argparse
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError:
    print("Error: pydantic is required. Please install it using 'pip install pydantic'")
    exit(1)

class NodeType(str, Enum):
    AGENT = "Agent"
    TOOL = "Tool" 
    CUSTOM_TOOL = "CustomTool"
    PREDEFINED_TOOL = "PredefinedTool"
    START = "Start"
    END = "End" 

class ToolType(str, Enum): 
    DEFAULT = "Default"

class ToolDefinition(BaseModel):
    name: str
    custom: bool
    description: Optional[str] = None

class AgentDefinition(BaseModel):
    name: str 
    tools: list[ToolDefinition] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list) 
    variable_name: Optional[str] = None 
    source_location: Optional[Dict] = None 
    model_ref: Optional[str] = None 

class InvalidAgentDefinitionError(Exception): pass
class InvalidHandoffSpecError(Exception): pass
class ToolDefinitionNotFoundError(Exception): pass


def get_positional_arg_node(call_node: ast.Call, index: int) -> Optional[ast.AST]:
    if not isinstance(call_node, ast.Call): raise TypeError("Expected an ast.Call node")
    if index < 0: return None
    return call_node.args[index] if index < len(call_node.args) else None

def get_kwarg_node(call_node: ast.Call, keyword: str) -> Optional[ast.AST]:
    if not isinstance(call_node, ast.Call): raise TypeError("Expected an ast.Call node")
    for kw in call_node.keywords:
        if kw.arg == keyword: return kw.value
    return None

def is_name_or_attr(node: ast.AST) -> bool:
    if isinstance(node, ast.Name): return True
    if isinstance(node, ast.Attribute): return True
    if isinstance(node, ast.Subscript): 
        return is_name_or_attr(node.value)
    return False

def get_identifier_string(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name): return node.id
    elif isinstance(node, ast.Attribute):
        try:
            if hasattr(ast, 'unparse'): return ast.unparse(node)
            base = get_identifier_string(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        except: return node.attr 
    elif isinstance(node, ast.Subscript): 
        return get_identifier_string(node.value)
    return None

def is_matching_call(node: ast.AST, target_names: Set[str]) -> bool:
    if not isinstance(node, ast.Call): return False
    func_node = node.func 
    base_name = get_identifier_string(func_node)
    if base_name and base_name in target_names:
        return True
    if isinstance(func_node, ast.Attribute) and func_node.attr in target_names:
         return True
    return False

def find_decorator_node(node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef], target_names: Set[str]) -> Optional[ast.AST]:
    for decorator in node.decorator_list:
        deco_node_to_check = decorator
        if isinstance(decorator, ast.Call):
            deco_node_to_check = decorator.func 
        deco_name = get_identifier_string(deco_node_to_check)
        if deco_name and deco_name in target_names:
            return decorator 
    return None

def extract_string_literal(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None

def extract_kwarg_string(call_node: ast.Call, keyword: str) -> Optional[str]:
     value_node = get_kwarg_node(call_node, keyword)
     return extract_string_literal(value_node)

def get_qualified_name(node: Union[ast.Name, ast.Attribute, ast.Call, ast.Subscript]) -> str:
    if isinstance(node, ast.Name): return node.id
    elif isinstance(node, ast.Attribute):
        base = get_qualified_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    elif isinstance(node, ast.Call): return get_qualified_name(node.func)
    elif isinstance(node, ast.Subscript): return get_qualified_name(node.value) 
    return ""

def represent_node(node: Optional[ast.AST]) -> str:
    if node is None: return "None"
    if isinstance(node, ast.Constant): return repr(node.value)
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        name = get_qualified_name(node)
        return name if name else f"<Attribute {node.attr}>"
    if isinstance(node, ast.Subscript): 
        base = represent_node(node.value)
        slice_val = represent_node(node.slice)
        return f"{base}[{slice_val}]"
    if isinstance(node, ast.List): return f"[{', '.join(represent_node(elt) for elt in node.elts)}]"
    if isinstance(node, ast.Call):
         func_str = represent_node(node.func)
         args_str = ', '.join(represent_node(arg) for arg in node.args)
         kwargs_str = ', '.join(f"{kw.arg}={represent_node(kw.value)}" for kw in node.keywords if kw.arg)
         all_args = f"{args_str}{', ' if args_str and kwargs_str else ''}{kwargs_str}"
         return f"{func_str}({all_args})"
    else:
        try:
            if hasattr(ast, 'unparse'): return ast.unparse(node)
            return f"<{type(node).__name__}>"
        except Exception: return f"<{type(node).__name__}>"

def create_location_info(node: ast.AST, current_filepath: str) -> Optional[Dict[str, Any]]:
    if not current_filepath or not hasattr(node, 'lineno'): return None
    end_lineno = getattr(node, 'end_lineno', node.lineno)
    end_col_offset = getattr(node, 'end_col_offset', -1)
    col_offset = getattr(node, 'col_offset', -1) 
    return {
        "file": current_filepath,
        "line": node.lineno, "col": col_offset,
        "end_line": end_lineno, "end_col": end_col_offset,
    }

class ToolDefinitionExtractor(ast.NodeVisitor):
    DECORATOR_IDENTS = {"function_tool", "openai.beta.tools.function_tool"}
    CONSTRUCTOR_IDENT = "FunctionTool" 

    def __init__(self, filepath: str) -> None:
        super().__init__()
        self.current_filepath = filepath
        self.discovered_tools: dict[str, ToolDefinition] = {}
        self.tool_locations: dict[str, Dict] = {}

    def visit_FunctionDef(self, node): self._process_function(node)
    def visit_AsyncFunctionDef(self, node): self._process_function(node)

    def visit_Assign(self, node: ast.Assign):
        if isinstance(node.value, ast.Call) and is_matching_call(node.value, {self.CONSTRUCTOR_IDENT}):
            call_node = node.value
            tool_name = extract_kwarg_string(call_node, "name")
            if not tool_name:
                print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Found '{self.CONSTRUCTOR_IDENT}' assignment without 'name' string arg. Skipping.")
                return

            description = extract_kwarg_string(call_node, "description") or ""
            location = create_location_info(node, self.current_filepath)
            tool_def = ToolDefinition(name=tool_name, custom=True, description=description)

            for target in node.targets:
                target_name = get_identifier_string(target)
                if target_name:
                    self.discovered_tools[target_name] = tool_def
                    if location: self.tool_locations[target_name] = location
                else:
                     print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Cannot assign '{self.CONSTRUCTOR_IDENT}' to complex target: {represent_node(target)}")
        self.generic_visit(node)

    def _process_function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        decorator_node = find_decorator_node(node, self.DECORATOR_IDENTS)
        if not decorator_node:
            self.generic_visit(node)
            return

        func_name = node.name
        tool_name = func_name 
        description = ast.get_docstring(node) or ""
        location = create_location_info(node, self.current_filepath)

        if isinstance(decorator_node, ast.Call):
            name_override = extract_kwarg_string(decorator_node, "name_override")
            if name_override: tool_name = name_override
            desc_override = extract_kwarg_string(decorator_node, "description_override")
            if desc_override: description = desc_override

        tool_def = ToolDefinition(name=tool_name, custom=True, description=description)
        self.discovered_tools[func_name] = tool_def
        if location:
             self.tool_locations[tool_name] = location
             if tool_name != func_name:
                 self.tool_locations[func_name] = location
        self.generic_visit(node)

def gather_tool_definitions(root_path: str) -> Tuple[dict[str, ToolDefinition], dict[str, Dict]]:
    all_tools: dict[str, ToolDefinition] = {}
    all_locations: dict[str, Dict] = {}
    for file_path in Path(root_path).rglob("*.py"):
        filepath_str = str(file_path)
        try:
            with open(file_path, "r", encoding='utf-8') as f: content = f.read()
            tree = ast.parse(content, filename=filepath_str)
            extractor = ToolDefinitionExtractor(filepath_str)
            extractor.visit(tree)
            all_tools.update(extractor.discovered_tools)
            all_locations.update(extractor.tool_locations)
        except SyntaxError as e: print(f"Warning: Skipping file {filepath_str} due to SyntaxError: {e}")
        except Exception as e: print(f"Warning: Skipping file {filepath_str} due to unexpected error: {e}") 

    print(f"Found {len(all_tools)} custom tool definitions/references.")
    return all_tools, all_locations

def load_existing_tool_defs(json_filename: str = "existing_tools.json") -> dict[str, ToolDefinition]:
    existing_tools: dict[str, ToolDefinition] = {}
    try:
        script_dir = Path(__file__).parent
        full_path = script_dir / json_filename
        if not full_path.exists():
             full_path = Path(json_filename)
             if not full_path.exists():
                  print(f"Info: Existing tools file not found at '{script_dir / json_filename}' or '{Path.cwd() / json_filename}'.")
                  return {}

        print(f"Loading existing tools from: {full_path}")
        with open(full_path, 'r', encoding='utf-8') as f: tool_data = json.load(f)
        if not isinstance(tool_data, dict): raise ValueError("JSON must contain an object")

        for tool_id, description in tool_data.items():
            if not isinstance(tool_id, str) or not isinstance(description, str):
                print(f"Warning: Skipping invalid entry in existing tools (key/value not strings): {tool_id}")
                continue
            existing_tools[tool_id] = ToolDefinition(name=tool_id, custom=False, description=description)
        print(f"Loaded {len(existing_tools)} existing tools.")
        return existing_tools
    except Exception as e:
        print(f"Error loading existing tools from '{json_filename}': {e}")
        return {}

class AgentDefinitionExtractor(ast.NodeVisitor):
    AGENT_IDENTS = {"Agent", "openai.beta.agents.Agent"}
    HANDOFF_HELPER_IDENT: Optional[str] = "handoff" 

    def __init__(
        self,
        filepath: str,
        available_tools: dict[str, ToolDefinition],
        accumulated_agents: dict[str, AgentDefinition], 
        var_agent_link: dict[str, str] 
    ):
        super().__init__()
        self.current_filepath = filepath
        self.available_tools = available_tools
        self.parsed_agents = accumulated_agents
        self.var_agent_mapping = var_agent_link
        self.local_handoff_vars: dict[str, str] = {} 

    def visit_Assign(self, node: ast.Assign):
        if isinstance(node.value, ast.Call):
            call_node = node.value
            if is_matching_call(call_node, self.AGENT_IDENTS):
                self._process_agent_assignment(node)
            elif self.HANDOFF_HELPER_IDENT and is_matching_call(call_node, {self.HANDOFF_HELPER_IDENT}):
                 self._process_handoff_var_assignment(node)
        elif isinstance(node.value, ast.Name) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
             source_var = node.value.id
             target_var = node.targets[0].id
             if source_var in self.var_agent_mapping:
                  self.var_agent_mapping[target_var] = self.var_agent_mapping[source_var]
        self.generic_visit(node)

    def _process_agent_assignment(self, node: ast.Assign):
        assert isinstance(node.value, ast.Call)
        call_node = node.value
        location = create_location_info(node, self.current_filepath)

        try:
            agent_def = self._parse_agent_call(call_node, location)
            if not agent_def: return
        except InvalidAgentDefinitionError as e:
            print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Invalid Agent constructor: {e}. Skipping.")
            return
        except Exception as e:
             print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Unexpected error parsing agent definition: {e}. Skipping.")
             return

        for target in node.targets:
            target_var_name = get_identifier_string(target) 
            if target_var_name:
                agent_def.variable_name = target_var_name
                agent_id = agent_def.name
                if agent_id in self.parsed_agents:
                     existing_var = self.parsed_agents[agent_id].variable_name
                     if existing_var and existing_var != target_var_name:
                          print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Agent name '{agent_id}' already linked to var '{existing_var}'. Re-linking to '{target_var_name}'.")
                self.parsed_agents[agent_id] = agent_def
                self.var_agent_mapping[target_var_name] = agent_id
            else:
                 print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Cannot assign Agent to complex target: {represent_node(target)}")

    def _process_handoff_var_assignment(self, node: ast.Assign):
        if not self.HANDOFF_HELPER_IDENT: return
        try:
            target_agent_ref = self._parse_handoff_call_target(node.value)
        except InvalidHandoffSpecError as e:
            print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Invalid handoff definition: {e}. Skipping.")
            return

        for target in node.targets:
            target_var_name = get_identifier_string(target)
            if target_var_name:
                self.local_handoff_vars[target_var_name] = target_agent_ref
            else:
                 print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Cannot assign handoff result to complex target: {represent_node(target)}")

    def _parse_agent_call(self, agent_call: ast.Call, location: Optional[Dict]) -> Optional[AgentDefinition]:
        try:
            name = extract_kwarg_string(agent_call, "name")
            if not name:
                 first_arg = get_positional_arg_node(agent_call, 0)
                 name = extract_string_literal(first_arg)
                 if not name: raise ValueError("Agent missing 'name' string arg")

            tools_list = get_kwarg_node(agent_call, "tools")
            tools: List[ToolDefinition] = []
            if isinstance(tools_list, ast.List):
                tools = self._identify_tools(tools_list.elts)

            handoffs_list = get_kwarg_node(agent_call, "handoffs")
            handoffs: List[str] = []
            if isinstance(handoffs_list, ast.List):
                 handoffs = self._identify_handoffs(handoffs_list.elts)

            model_ref: Optional[str] = None
            model_node = get_kwarg_node(agent_call, "model")
            if model_node:
                model_name_literal = extract_string_literal(model_node)
                if model_name_literal:
                    model_ref = model_name_literal
                else:
                    model_ref = represent_node(model_node)

            return AgentDefinition(name=name, tools=tools, handoffs=handoffs, source_location=location , model_ref=model_ref)
        except (ValueError, TypeError, ValidationError, ToolDefinitionNotFoundError) as e:
            raise InvalidAgentDefinitionError(f"Failed extraction for '{represent_node(agent_call)}': {e}") from e

    def _identify_tools(self, tool_nodes: List[ast.AST]) -> List[ToolDefinition]:
        identified_tools = []
        for node in tool_nodes:
            tool_ref = get_identifier_string(node) 
            if tool_ref:
                if tool_ref in self.available_tools:
                    identified_tools.append(self.available_tools[tool_ref])
                else:
                     print(f"Warning [{self.current_filepath}]: Unknown tool reference '{tool_ref}'. Skipping.")
            else:
                print(f"Warning [{self.current_filepath}]: Unsupported item in tools list: {represent_node(node)}. Skipping.")
        return identified_tools

    def _identify_handoffs(self, handoff_nodes: List[ast.AST]) -> List[str]:
         identified_targets = []
         for node in handoff_nodes:
             target_ref: Optional[str] = None
             agent_ref = get_identifier_string(node)
             if agent_ref:
                  target_ref = agent_ref 
             elif self.HANDOFF_HELPER_IDENT and isinstance(node, ast.Call) and is_matching_call(node, {self.HANDOFF_HELPER_IDENT}):
                 try:
                     target_ref = self._parse_handoff_call_target(node)
                 except InvalidHandoffSpecError as e:
                      print(f"Warning [{self.current_filepath}]: Invalid handoff() in list: {e}. Skipping.")

             if target_ref:
                 if target_ref in self.local_handoff_vars:
                      identified_targets.append(self.local_handoff_vars[target_ref])
                 else:
                      identified_targets.append(target_ref)
             else:
                 print(f"Warning [{self.current_filepath}]: Unsupported item in handoffs list: {represent_node(node)}. Skipping.")
         return identified_targets

    def _parse_handoff_call_target(self, handoff_call: ast.Call) -> str:
        if not self.HANDOFF_HELPER_IDENT: raise InvalidHandoffSpecError("Handoff helper not configured.")
        target_node = get_kwarg_node(handoff_call, "agent")
        if not target_node: target_node = get_positional_arg_node(handoff_call, 0)
        if not target_node: raise InvalidHandoffSpecError("handoff() call missing target agent.")
        target_name = get_identifier_string(target_node) 
        if not target_name: raise InvalidHandoffSpecError(f"handoff() target not simple identifier: {represent_node(target_node)}")
        return target_name 

def gather_agent_definitions(root_path: str, available_tools: dict[str, ToolDefinition]) -> Tuple[dict[str, AgentDefinition], dict[str, str]]:
    all_agents: dict[str, AgentDefinition] = {} 
    var_agent_link: dict[str, str] = {} 
    for file_path in Path(root_path).rglob("*.py"):
        filepath_str = str(file_path)
        try:
            with open(file_path, "r", encoding='utf-8') as f: content = f.read()
            tree = ast.parse(content, filename=filepath_str)
            extractor = AgentDefinitionExtractor(filepath_str, available_tools, all_agents, var_agent_link)
            extractor.visit(tree)
        except SyntaxError as e: print(f"Warning: Skipping file {filepath_str} due to SyntaxError: {e}")
        except Exception as e: print(f"Warning: Skipping file {filepath_str} due to unexpected error: {e}") 

    print(f"Found {len(all_agents)} agent definitions.")
    return all_agents, var_agent_link

class ExecutionStartFinder(ast.NodeVisitor):
    RUNNER_IDENTS = {"Runner", "openai.beta.agents.Runner"} 
    METHOD_IDENT = "run"

    def __init__(self, filepath: str):
        self.current_filepath = filepath
        self.start_points: List[Tuple[str, Dict]] = [] 

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute):
            attr_node = node.func
            method_name = attr_node.attr
            base_node = attr_node.value 
            base_name = get_identifier_string(base_node) 
            is_run_call = (method_name == self.METHOD_IDENT and base_name in self.RUNNER_IDENTS)

            if is_run_call:
                location = create_location_info(node, self.current_filepath)
                if node.args:
                    agent_arg = node.args[0]
                    agent_ref = get_identifier_string(agent_arg) 
                    if agent_ref and location:
                        self.start_points.append((agent_ref, location))
                    else:
                         agent_ref_str = represent_node(agent_arg)
                         print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Found {base_name}.{method_name} call unable to resolve agent arg '{agent_ref_str}' to simple name.")
                         if agent_ref_str and location: 
                              self.start_points.append((agent_ref_str, location))
                else:
                     print(f"Warning [{self.current_filepath}:{getattr(node, 'lineno', '?')}]: Found {base_name}.{method_name} call with no arguments.")
        self.generic_visit(node)

def find_execution_starts(root_path: str) -> List[Tuple[str, Dict]]:
     all_starts = []
     for file_path in Path(root_path).rglob("*.py"):
        filepath_str = str(file_path)
        try:
            with open(file_path, "r", encoding='utf-8') as f: content = f.read()
            tree = ast.parse(content, filename=filepath_str)
            finder = ExecutionStartFinder(filepath_str)
            finder.visit(tree)
            all_starts.extend(finder.start_points)
        except SyntaxError as e: print(f"Warning: Skipping file {filepath_str} due to SyntaxError: {e}")
        except Exception as e: print(f"Warning: Skipping file {filepath_str} due to unexpected error: {e}")
     return all_starts

def build_graph_json(
    agents: dict[str, AgentDefinition], 
    custom_tools: dict[str, ToolDefinition], 
    custom_tool_locs: dict[str, Dict], 
    existing_tools: dict[str, ToolDefinition], 
    start_points: List[Tuple[str, Dict]], 
    var_agent_link: Dict[str, str] 
    ) -> Dict[str, List[Dict]]:

    nodes: List[Dict] = []
    edges: List[Dict] = []
    processed_ids: Set[str] = set()

    for agent_id, agent_def in agents.items():
        if agent_id in processed_ids: continue
        node_meta = {
            "variable_name": agent_def.variable_name,
            "tool_names": [t.name for t in agent_def.tools],
            "handoff_refs": agent_def.handoffs, 
            "model": agent_def.model_ref
        }
        node_meta = {k: v for k, v in node_meta.items() if v is not None}
        node = {
            "id": agent_def.name, "name": agent_def.name,
            "node_type": NodeType.AGENT.value,
            "source_location": agent_def.source_location,
            "metadata": node_meta 
        }
        nodes.append(node)
        processed_ids.add(agent_id)

    agent_tool_names = set()
    for agent_def in agents.values():
        for tool in agent_def.tools:
            agent_tool_names.add(tool.name) 

    for tool_id in agent_tool_names:
        if tool_id in processed_ids: continue 
        tool_def = custom_tools.get(tool_id)
        is_custom = True
        if not tool_def:
            tool_def = existing_tools.get(tool_id)
            is_custom = False
        if not tool_def:
             print(f"Warning: Tool '{tool_id}' referenced but definition missing. Skipping node.")
             continue 

        node_type = NodeType.CUSTOM_TOOL if is_custom else NodeType.PREDEFINED_TOOL
        location = custom_tool_locs.get(tool_id) if is_custom else None
        tool_node = {
            "id": tool_id, "name": tool_id,
            "function_name": tool_id if is_custom else None,
            "docstring": tool_def.description,
            "node_type": node_type.value,
            "source_location": location,
            "metadata": {"custom": is_custom}
        }
        nodes.append(tool_node)
        processed_ids.add(tool_id)

    for agent_id, agent_def in agents.items():
        agent_loc = agent_def.source_location
        for tool_def in agent_def.tools:
            tool_name = tool_def.name
            if tool_name in processed_ids: 
                edges.append({
                    "source": agent_id, "target": tool_name,
                    "edge_type": "tool_usage", "condition": {},
                    "metadata": {"definition_location": agent_loc}
                })

    for agent_id, agent_def in agents.items():
        agent_loc = agent_def.source_location
        for handoff_ref in agent_def.handoffs:
            target_id = var_agent_link.get(handoff_ref) 
            if not target_id: target_id = handoff_ref
            if target_id in agents: 
                edges.append({
                    "source": agent_id, "target": target_id,
                    "edge_type": "handoff", "condition": {},
                    "metadata": {"definition_location": agent_loc, "handoff_ref": handoff_ref}
                })
            else:
                 print(f"Warning: Agent '{agent_id}' handoff ref '{handoff_ref}' (->'{target_id}') not found.")

    start_id, end_id = "Start", "End"
    if start_id not in processed_ids: nodes.append({"id": "Start", "name": "Start", "node_type": NodeType.START.value}); processed_ids.add(start_id)
    if end_id not in processed_ids: nodes.append({"id": "End", "name": "End", "node_type": NodeType.END.value}); processed_ids.add(end_id)

    runner_starts = set()
    for agent_ref, loc in start_points:
        agent_id = var_agent_link.get(agent_ref) 
        if agent_id and agent_id in agents:
            if not any(e['source'] == start_id and e['target'] == agent_id for e in edges): 
                edges.append({
                    "source": start_id, "target": agent_id,
                    "edge_type": "execution_start", "condition": {},
                    "metadata": {"definition_location": loc, "runner_agent_ref": agent_ref}
                })
            runner_starts.add(agent_id)
        else:
            print(f"Warning: Runner start ref '{agent_ref}' not mapped to known agent.")

    agent_ids = set(agents.keys())
    incoming: Dict[str, int] = {name: 0 for name in agent_ids}
    outgoing: Dict[str, int] = {name: 0 for name in agent_ids}
    for edge in edges: 
        if edge['edge_type'] == 'handoff' and edge['source'] in agent_ids and edge['target'] in agent_ids:
            incoming[edge['target']] = incoming.get(edge['target'], 0) + 1
            outgoing[edge['source']] = outgoing.get(edge['source'], 0) + 1

    for agent_id in agent_ids:
        if incoming.get(agent_id, 0) == 0 and agent_id not in runner_starts:
            if not any(e['source'] == start_id and e['target'] == agent_id for e in edges):
                edges.append({"source": start_id, "target": agent_id, "edge_type": "implicit_start", "condition": {}, "metadata": {}})
    for agent_id in agent_ids:
        if outgoing.get(agent_id, 0) == 0:
             if not any(e['source'] == agent_id and e['target'] == end_id for e in edges):
                 edges.append({"source": agent_id, "target": end_id, "edge_type": "implicit_end", "condition": {}, "metadata": {}})

    for node in nodes:
        node.setdefault("function_name", None)
        node.setdefault("docstring", None)
        node.setdefault("source_location", None)
        node.setdefault("metadata", {})

    for node in nodes:
        if "metadata" in node:
             node["metadata"] = {k:v for k,v in node["metadata"].items() if v is not None}


    return {"nodes": nodes, "edges": edges}



def extract_openai_agents_graph(scan_path: str, output_file: str):
    existing_tools_file = "existing_tools.json" # Hardcoded path

    if not os.path.isdir(scan_path):
        print(f"Error: Path '{scan_path}' is not a valid directory.")
        sys.exit(1)


    print(f"Analyzing agent structures in: {scan_path}")

    try:
        custom_tools, custom_locs = gather_tool_definitions(scan_path)
        existing_tools = load_existing_tool_defs(existing_tools_file)
        available_tools = {**existing_tools, **custom_tools}
        print(f"Total available tools: {len(available_tools)}")

        agents, var_agent_link = gather_agent_definitions(scan_path, available_tools)

        start_points = find_execution_starts(scan_path)
        print(f"Found {len(start_points)} potential execution starts.")

        final_graph = build_graph_json(
            agents=agents,
            custom_tools=custom_tools,
            custom_tool_locs=custom_locs,
            existing_tools=existing_tools,
            start_points=start_points,
            var_agent_link=var_agent_link
        )
        print(f"Analysis complete. Nodes: {len(final_graph['nodes'])}, Edges: {len(final_graph['edges'])}.")

        if final_graph:
            final_graph["metadata"] = {
                "framework": "OpenAI_Agents",
            }

    except Exception as e:
         print(f"Error during graph extraction: {e}")
         sys.exit(1)


    if final_graph["nodes"] or final_graph["edges"]:
        try:

            final_graph["nodes"].sort(key=lambda x: x.get('id', ''))
            final_graph["edges"].sort(key=lambda x: (x.get('source', ''), x.get('target', ''), x.get('edge_type', '')))


            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding='utf-8') as f:
                json.dump(final_graph, f, indent=2)
            print(f"\nGraph structure written to {output_path}")


            print("\n--- Summary ---")
            if not final_graph["nodes"]: print("No nodes found.")
            for node in final_graph["nodes"]:
                 loc = node.get('source_location')
                 loc_str = f" ({loc['file']}:{loc['line']})" if loc and loc.get('file') else ""
                 node_id = node.get('id', 'Unknown ID')
                 node_type = node.get('node_type', 'Unknown Type')
                 print(f"Node: {node_id} ({node_type}){loc_str}")

            if not final_graph["edges"]: print("No edges found.")
            for edge in final_graph["edges"]:
                 cond_str = f" Cond: {edge.get('condition')}" if edge.get('condition') else ""
                 meta_loc = edge.get('metadata', {}).get('definition_location')
                 loc_str = f" ({meta_loc['file']}:{meta_loc['line']})" if meta_loc and meta_loc.get('file') else ""
                 source = edge.get('source', 'Unknown')
                 target = edge.get('target', 'Unknown')
                 edge_type = edge.get('edge_type', 'Unknown Type')
                 print(f"Edge: {source} -> {target} ({edge_type}){cond_str}{loc_str}")

        except Exception as e:
            print(f"Error writing JSON output or printing summary: {e}")
            sys.exit(1)
    else:
        print("\nNo graph structure found or extracted.")
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
    parser = argparse.ArgumentParser(description="Parse Python code to extract OpenAI Agent structures into JSON.")
    parser.add_argument("directory", type=str, help="Directory containing the Python code.")
    parser.add_argument("-o", "--output", type=str, default="agent_graph_structure.json", help="Output JSON filename.")

    args = parser.parse_args()

    target_scan_dir = args.directory
    output_file = args.output


    extract_openai_agents_graph(target_scan_dir, output_file)