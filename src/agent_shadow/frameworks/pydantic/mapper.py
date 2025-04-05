# -*- coding: utf-8 -*-
import argparse
import ast
import json
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum
import inspect

# --- Helper Functions ---

def get_potential_fqn(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        base = get_potential_fqn(node.value); return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Subscript):
         return get_potential_fqn(node.value)
    return None

def node_to_string(node: ast.AST) -> str:
    if node is None: return "None"
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return get_potential_fqn(node) or "<Attribute>"
    if isinstance(node, ast.Constant): return repr(node.value)
    if isinstance(node, ast.Subscript):
        value_str = node_to_string(node.value)
        slice_node = node.slice
        if hasattr(slice_node, 'value') and not isinstance(slice_node, ast.Tuple): slice_node = slice_node.value
        if isinstance(slice_node, ast.Tuple): slice_str = ", ".join(node_to_string(elt) for elt in slice_node.elts)
        else: slice_str = node_to_string(slice_node)
        return f"{value_str}[{slice_str}]"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = node_to_string(node.left); right = node_to_string(node.right); return f"{left} | {right}"
    if isinstance(node, ast.Call):
        func_name = get_potential_fqn(node.func); return f"{func_name}(...)" if func_name else "<Call>"
    if isinstance(node, ast.Compare):
        left = node_to_string(node.left)
        op = node.ops[0]
        comp = node_to_string(node.comparators[0])
        op_str = "==" if isinstance(op, ast.Eq) else "!=" if isinstance(op, ast.NotEq) else str(op)
        return f"{left} {op_str} {comp}"
    try: return f"<ast.{type(node).__name__}>"
    except: return "<unknown_node>"


def get_definition_location(node: ast.AST, filepath: Optional[str]) -> str:
    line = getattr(node, 'lineno', '?'); col = getattr(node, 'col_offset', '?')
    filepath_str = str(filepath) if filepath else "unknown_file"; return f"{filepath_str}:{line}:{col}"

# --- Enums ---
class PydanticAINodeType(str, Enum):
    AGENT = "Agent"
    TOOL = "Tool"
    ORCHESTRATOR = "Orchestrator"
    START = "Start"
    END = "End"

# --- Internal Visitor for Finding Agent Calls ---
class AgentCallFinder(ast.NodeVisitor):
    def __init__(self):
        self.called_agents: List[Tuple[str, ast.Call]] = []

    def visit_Await(self, node: ast.Await):
        if isinstance(node.value, ast.Call):
            call_node = node.value
            if isinstance(call_node.func, ast.Attribute) and call_node.func.attr == 'run':
                agent_var_name = get_potential_fqn(call_node.func.value)
                if agent_var_name: self.called_agents.append((agent_var_name, call_node))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
         if isinstance(node.func, ast.Attribute) and node.func.attr == 'run':
             agent_var_name = get_potential_fqn(node.func.value)
             if agent_var_name: self.called_agents.append((agent_var_name, node))
         self.generic_visit(node)

# --- Main AST Visitor Class ---
class PydanticAIStructureExtractor(ast.NodeVisitor):
    def __init__(self):
        self.agents_info: Dict[str, Dict[str, Any]] = {}
        self.tools_info: Dict[str, Dict[str, Any]] = {}
        self.deps_classes: Dict[str, Dict[str, Any]] = {}
        self.orchestrator_funcs: Dict[str, Dict[str, Any]] = {}
        self.imports: Dict[str, str] = {}
        self.pydantic_ai_fqns = { "Agent": "pydantic_ai.agent.Agent", "RunContext": "pydantic_ai.context.RunContext" }
        self.current_filepath: Optional[str] = None
        self.potential_entry_points: List[str] = []
        self.output_nodes: List[Dict[str, Any]] = []
        self.output_edges: List[Dict[str, Any]] = []
        self.output_node_ids: Set[str] = set()

    def _get_joined_string_value(self, node: ast.JoinedStr):
        return "".join(
            str(part.value) if isinstance(part, ast.Constant) else f"{{{node_to_string(part.value)}}}" if isinstance(part, ast.FormattedValue) else f"<complex_part:{type(part).__name__}>"
            for part in node.values
        )

    def _add_output_node(self, name: str, node_type: PydanticAINodeType, docstring: Optional[str] = None, description: Optional[str] = None, location: Optional[str] = None, metadata: Optional[Dict] = None):
        if name not in self.output_node_ids:
            node_data = {
                "name": name,
                "function_name": name if node_type in [PydanticAINodeType.TOOL, PydanticAINodeType.ORCHESTRATOR] else None,
                "docstring": docstring,
                "description": description or "",
                "node_type": node_type.value,
                "location": location or "unknown",
                "metadata": metadata or {}
            }
            if node_type == PydanticAINodeType.AGENT: node_data["function_name"] = name
            self.output_nodes.append(node_data)
            self.output_node_ids.add(name)

    def _add_output_edge(self, source: str, target: str, condition: Optional[str] = None, definition_location: Optional[str] = None, metadata: Optional[Dict] = None):
        if source in self.output_node_ids and target in self.output_node_ids:
            edge_sig = (source, target, condition)
            if not any(e['source'] == source and e['target'] == target and e['condition'] == condition for e in self.output_edges):
                edge_metadata = metadata or {}
                if definition_location: edge_metadata["definition_location"] = definition_location
                self.output_edges.append({"source": source, "target": target, "condition": condition or "", "metadata": edge_metadata})

    def _resolve_fqn_from_node(self, node: ast.AST) -> Optional[str]:
        potential_name = get_potential_fqn(node)
        if potential_name:
             if potential_name in self.imports: return self.imports[potential_name]
             parts = potential_name.split('.');
             if len(parts) > 1 and parts[0] in self.imports: return f"{self.imports[parts[0]]}.{'.'.join(parts[1:])}"
             return potential_name
        return None

    def _get_string_value(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
        if isinstance(node, ast.JoinedStr):
             try: return "".join(str(v.value) for v in node.values if isinstance(v, ast.Constant))
             except: return "<f-string>"
        return None

    def _get_list_of_identifiers(self, node: ast.AST) -> Optional[List[str]]:
        if isinstance(node, ast.List):
            elements = []
            for elt in node.elts:
                name = get_potential_fqn(elt)
                if name: elements.append(name)
                elif isinstance(elt, ast.Call):
                     call_name = get_potential_fqn(elt.func);
                     if call_name: elements.append(f"{call_name}()")
                else: elements.append(node_to_string(elt))
            return elements
        return None

    def _get_call_kwargs(self, node: ast.Call) -> Dict[str, Any]:
        kwargs = {}
        for keyword in node.keywords:
            arg_name = keyword.arg
            if arg_name:
                value_node = keyword.value
                if isinstance(value_node, ast.Constant): kwargs[arg_name] = value_node.value
                elif isinstance(value_node, (ast.Name, ast.Attribute, ast.BinOp, ast.Subscript)): kwargs[arg_name] = node_to_string(value_node)
                elif isinstance(value_node, ast.List): kwargs[arg_name] = self._get_list_of_identifiers(value_node) or "<complex_list>"
                elif isinstance(value_node, ast.JoinedStr): kwargs[arg_name] = self._get_joined_string_value(value_node)
                else: kwargs[arg_name] = f"<ast.{type(value_node).__name__}>"
        return kwargs

    def visit(self, node: ast.AST, filepath: Optional[str] = None):
        original_path = self.current_filepath
        if filepath: self.current_filepath = filepath
        super().visit(node)
        self.current_filepath = original_path

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_name = alias.name; local_name = alias.asname or module_name
            self.imports[local_name] = module_name
            if module_name == 'pydantic_ai':
                 self.pydantic_ai_fqns['Agent'] = 'pydantic_ai.Agent'; self.pydantic_ai_fqns['RunContext'] = 'pydantic_ai.RunContext'
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            base_module = node.module
            for alias in node.names:
                imported_name = alias.name; local_name = alias.asname or imported_name
                full_path = f"{base_module}.{imported_name}"; self.imports[local_name] = full_path
                for key, default_fqn in self.pydantic_ai_fqns.items():
                    potential_fqn_match = full_path
                    if node.level > 0 and self.current_filepath:
                        relative_prefix = "." * node.level
                        potential_fqn_match = f"{relative_prefix}{base_module}.{imported_name}"
                    if potential_fqn_match == default_fqn:
                        self.pydantic_ai_fqns[key] = local_name
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        if isinstance(node.test, ast.Compare):
            comp = node.test
            if isinstance(comp.left, ast.Name) and comp.left.id == '__name__' and \
               len(comp.ops) == 1 and isinstance(comp.ops[0], ast.Eq) and \
               len(comp.comparators) == 1 and isinstance(comp.comparators[0], ast.Constant) and \
               comp.comparators[0].value == '__main__':
                for sub_node in ast.walk(node):
                    if isinstance(sub_node, ast.Call):
                        if isinstance(sub_node.func, ast.Attribute) and \
                           sub_node.func.attr == 'run' and \
                           get_potential_fqn(sub_node.func.value) in ['asyncio', self.imports.get('asyncio')]:
                            if len(sub_node.args) == 1 and isinstance(sub_node.args[0], ast.Call):
                                potential_entry_call = sub_node.args[0]
                                func_name = get_potential_fqn(potential_entry_call.func)
                                if func_name and func_name not in self.potential_entry_points:
                                    self.potential_entry_points.append(func_name)
                        else:
                            func_name = get_potential_fqn(sub_node.func)
                            if func_name and func_name not in self.potential_entry_points:
                                self.potential_entry_points.append(func_name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        class_name = node.name; definition_loc = get_definition_location(node, self.current_filepath)
        is_pydantic_model = any(self._resolve_fqn_from_node(b) in ['pydantic.main.BaseModel', 'pydantic.BaseModel'] or get_potential_fqn(b) == 'BaseModel' for b in node.bases)
        is_dataclass = any((isinstance(d, ast.Name) and d.id == 'dataclass') or (isinstance(d, ast.Attribute) and d.attr == 'dataclass') or (isinstance(d, ast.Call) and get_potential_fqn(d.func) == 'dataclass') for d in node.decorator_list)
        class_node_ref = node
        if is_pydantic_model or is_dataclass:
             fields = []
             for body_node in node.body:
                 if isinstance(body_node, ast.AnnAssign) and isinstance(body_node.target, ast.Name):
                     field_name = body_node.target.id; field_type = node_to_string(body_node.annotation) if body_node.annotation else 'Any'
                     fields.append({"name": field_name, "type": field_type})
                 elif isinstance(body_node, ast.Assign) and len(body_node.targets) == 1 and isinstance(body_node.targets[0], ast.Name):
                      field_name = body_node.targets[0].id; fields.append({"name": field_name, "type": "Any"})
             self.deps_classes[class_name] = {"name": class_name, "fields": fields, "definition_location": definition_loc, "is_pydantic": is_pydantic_model, "is_dataclass": is_dataclass, "class_node": class_node_ref}
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            agent_var = node.targets[0].id
            value_node = node.value

            if isinstance(value_node, ast.Call):
                call_func = value_node.func
                base_func_node = None
                if isinstance(call_func, (ast.Name, ast.Attribute)):
                    base_func_node = call_func
                elif isinstance(call_func, ast.Subscript):
                    base_func_node = call_func.value

                if base_func_node:
                    resolved_func_fqn = self._resolve_fqn_from_node(base_func_node)
                    agent_fqn = self.pydantic_ai_fqns.get('Agent')
                    func_name_simple = get_potential_fqn(base_func_node)

                    is_agent_instantiation = (
                        (resolved_func_fqn and agent_fqn and resolved_func_fqn == agent_fqn) or
                        (func_name_simple == 'Agent') or
                        (resolved_func_fqn == 'pydantic_ai.agent.Agent')
                    )

                    if is_agent_instantiation:
                        kwargs = self._get_call_kwargs(value_node)
                        model_name = "unknown_model"
                        if value_node.args and len(value_node.args) > 0 and isinstance(value_node.args[0], (ast.Constant, ast.JoinedStr)):
                            model_name = self._get_string_value(value_node.args[0]) or self._get_joined_string_value(value_node.args[0]) or model_name
                        elif 'model' in kwargs:
                            model_name = str(kwargs['model'])

                        system_prompt = None
                        for kw in value_node.keywords:
                            if kw.arg == 'system_prompt':
                                if isinstance(kw.value, ast.JoinedStr): system_prompt = self._get_joined_string_value(kw.value)
                                else: system_prompt = self._get_string_value(kw.value)
                                if system_prompt is None: system_prompt = f"<complex_prompt:{type(kw.value).__name__}>"
                                break
                        if system_prompt is None and len(value_node.args) > 1 and isinstance(value_node.args[1], (ast.Constant, ast.JoinedStr)):
                             if isinstance(value_node.args[1], ast.JoinedStr): system_prompt = self._get_joined_string_value(value_node.args[1])
                             else: system_prompt = self._get_string_value(value_node.args[1])

                        result_type_str = kwargs.get("result_type", "Any")
                        deps_type_str = kwargs.get("deps_type")

                        definition_loc = get_definition_location(node, self.current_filepath)
                        # print(f"DEBUG: Found Agent '{agent_var}' defined at {definition_loc}")
                        self.agents_info[agent_var] = {
                            "name": agent_var, "model": model_name, "system_prompt": system_prompt,
                            "result_type_str": result_type_str, "deps_type_str": deps_type_str,
                            "tools": [], "definition_location": definition_loc,
                            "metadata_kwargs": kwargs
                        }
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        func_name = node.name; definition_loc = get_definition_location(node, self.current_filepath)
        is_tool = False; tool_agent_var = None

        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.attr == 'tool':
                potential_agent_var = get_potential_fqn(decorator.value)
                if potential_agent_var and potential_agent_var in self.agents_info:
                    is_tool = True; tool_agent_var = potential_agent_var;
                    # print(f"DEBUG: Found Tool '{func_name}' for Agent '{tool_agent_var}' at {definition_loc}")
                    break

        if is_tool and tool_agent_var:
            docstring = ast.get_docstring(node) or None
            self.tools_info[func_name] = {"name": func_name, "agent_var": tool_agent_var, "func_def_node": node, "docstring": docstring, "definition_location": definition_loc}
            if tool_agent_var in self.agents_info: self.agents_info[tool_agent_var]["tools"].append(func_name)
        else:
            finder = AgentCallFinder(); finder.visit(node)
            if finder.called_agents:
                 if func_name not in self.tools_info:
                     called_agent_names = list(set(agent_name for agent_name, _ in finder.called_agents))
                     call_locations = {}
                     processed_agents = set()
                     for agent_name, call_node in finder.called_agents:
                         if agent_name not in processed_agents:
                             call_locations[agent_name] = get_definition_location(call_node, self.current_filepath)
                             processed_agents.add(agent_name)

                     # print(f"DEBUG: Found Orchestrator '{func_name}' calling agents: {called_agent_names} at {definition_loc}")
                     self.orchestrator_funcs[func_name] = {
                         "name": func_name, "func_def_node": node, "definition_location": definition_loc,
                         "calls": called_agent_names, "call_locations": call_locations
                     }

        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def finalize_graph(self):
        print("Finalizing Pydantic-AI graph...")
        self._add_output_node(name="Start", node_type=PydanticAINodeType.START, description="Application Entry Point", location="system")
        self._add_output_node(name="End", node_type=PydanticAINodeType.END, description="Application Exit Point", location="system")

        # print(f"DEBUG: Finalizing Agents: {list(self.agents_info.keys())}")
        for agent_id, agent_data in self.agents_info.items():
            metadata_kwargs = agent_data.get("metadata_kwargs", {})
            metadata = {**metadata_kwargs, "model": agent_data.get("model"), "system_prompt": agent_data.get("system_prompt"), "result_type": agent_data.get("result_type_str"), "deps_type": agent_data.get("deps_type_str"), "tools": agent_data.get("tools")}
            description = agent_data.get("system_prompt") or f"Agent using model {agent_data.get('model', 'N/A')}"
            self._add_output_node(name=agent_id, node_type=PydanticAINodeType.AGENT, description=description, docstring=None, location=agent_data.get("definition_location"), metadata=metadata)

        # print(f"DEBUG: Finalizing Tools: {list(self.tools_info.keys())}")
        for tool_id, tool_data in self.tools_info.items():
             metadata = { "agent": tool_data.get("agent_var") }
             description = tool_data.get("docstring") or f"Tool function used by agent '{tool_data.get('agent_var')}'"
             self._add_output_node(name=tool_id, node_type=PydanticAINodeType.TOOL, description=description, docstring=tool_data.get("docstring"), location=tool_data.get("definition_location"), metadata=metadata)

        # print(f"DEBUG: Finalizing Orchestrators: {list(self.orchestrator_funcs.keys())}")
        for func_id, func_data in self.orchestrator_funcs.items():
             metadata = { "calls": func_data.get("calls") }
             docstring = ast.get_docstring(func_data.get("func_def_node")) or None
             description = docstring or f"Function orchestrating agent calls"
             self._add_output_node(name=func_id, node_type=PydanticAINodeType.ORCHESTRATOR, description=description, docstring=docstring, location=func_data.get("definition_location"), metadata=metadata)

        for agent_id, agent_data in self.agents_info.items():
             agent_def_loc = agent_data.get("definition_location")
             for tool_name in agent_data.get("tools", []):
                  if tool_name in self.tools_info:
                       tool_def_loc = self.tools_info[tool_name].get("definition_location")
                       # print(f"DEBUG: Adding Edge: {agent_id} --defines tool--> {tool_name}")
                       self._add_output_edge(source=agent_id, target=tool_name, condition="defines tool", definition_location=tool_def_loc, metadata={"agent_definition": agent_def_loc})

        for func_id, func_data in self.orchestrator_funcs.items():
             call_locations = func_data.get("call_locations", {})
             for called_agent_id in func_data.get("calls", []):
                  if called_agent_id in self.agents_info:
                      edge_def_loc = call_locations.get(called_agent_id)
                      # print(f"DEBUG: Adding Edge: {func_id} --calls agent--> {called_agent_id}")
                      self._add_output_edge(source=func_id, target=called_agent_id, condition="calls agent", definition_location=edge_def_loc, metadata={})

        for tool_id, tool_data in self.tools_info.items():
             calling_agent_id = tool_data.get("agent_var"); func_def_node = tool_data.get("func_def_node")
             if not calling_agent_id or not func_def_node: continue
             finder = AgentCallFinder(); finder.visit(func_def_node)
             processed_agents_in_tool = set()
             for called_agent_id_in_tool, call_node_in_tool in finder.called_agents:
                 if called_agent_id_in_tool not in processed_agents_in_tool:
                    tool_filepath = self.tools_info[tool_id].get("definition_location", "unknown").split(':')[0] if self.current_filepath is None else self.current_filepath
                    edge_def_loc_in_tool = get_definition_location(call_node_in_tool, tool_filepath)
                    processed_agents_in_tool.add(called_agent_id_in_tool)
                    if called_agent_id_in_tool in self.agents_info:
                        # print(f"DEBUG: Adding Edge: {calling_agent_id} --calls agent (via {tool_id})--> {called_agent_id_in_tool}")
                        self._add_output_edge(source=calling_agent_id, target=called_agent_id_in_tool, condition="calls agent", definition_location=edge_def_loc_in_tool, metadata={"via_tool": tool_id})

        print("Adding Start/End node connections based on entry points...")
        entry_points_connected_to_start = set()
        if self.potential_entry_points:
            for entry_point_name in self.potential_entry_points:
                if entry_point_name in self.output_node_ids:
                    self._add_output_edge(source="Start", target=entry_point_name, condition="initiates", definition_location="entry_point_detection")
                    entry_points_connected_to_start.add(entry_point_name)
        else:
            print("No specific __main__ entry points found. Using fallback: connecting Start to root orchestrators.")
            all_target_nodes = {edge['target'] for edge in self.output_edges}
            for node in self.output_nodes:
                node_id = node['name']; node_type = node['node_type']
                if node_type == PydanticAINodeType.ORCHESTRATOR.value and node_id not in all_target_nodes:
                    self._add_output_edge(source="Start", target=node_id, condition="initiates", definition_location="fallback_heuristic")
                    entry_points_connected_to_start.add(node_id)

        if entry_points_connected_to_start:
             for entry_point_name in entry_points_connected_to_start:
                 self._add_output_edge(source=entry_point_name, target="End", condition="terminates", definition_location="entry_point_detection")
        else:
            print("Warning: Could not identify any entry points to connect Start/End nodes.")


    def get_graph_data(self) -> Dict[str, List[Dict[str, Any]]]:
        return {"nodes": self.output_nodes, "edges": self.output_edges}

def extract_pydantic_ai_graph(directory_path: str, output_filename: str):
    """
    Extracts the Pydantic-AI graph structure from the given directory and saves it to a JSON file.

    Args:
        directory_path (str): The directory to scan for Python files.
        output_filename (str): The file path to save the extracted graph data.

    Returns:
        Dict[str, List[Dict[str, Any]]]: The extracted graph data containing nodes and edges.
    """
    extractor = PydanticAIStructureExtractor()
    print(f"Starting Pydantic-AI structure extraction in: {directory_path}")
    if not os.path.isdir(directory_path):
        print(f"Error: Provided path '{directory_path}' is not a valid directory.", file=sys.stderr)
        return {"nodes": [], "edges": []}

    parsed_files = 0
    filepaths_processed = []
    for root, dirs, files in os.walk(directory_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', 'env', '__pycache__', 'node_modules', '.git']]
        for filename in files:
            if filename.endswith(".py"):
                filepath = os.path.join(root, filename)
                filepaths_processed.append(filepath)
                try:
                    with open(filepath, "r", encoding='utf-8') as f:
                        content = f.read()
                    tree = ast.parse(content, filename=filepath)
                    extractor.visit(tree, filepath=filepath)
                    parsed_files += 1
                except SyntaxError as e:
                    print(f"Warning: Skipping file {filepath} due to SyntaxError: {e}", file=sys.stderr)
                    filepaths_processed.pop()
                except Exception as e:
                    print(f"Warning: Skipping file {filepath} due to error: {e}", file=sys.stderr)
                    import traceback
                    traceback.print_exc()
                    filepaths_processed.pop()

    print(f"AST parsing complete ({parsed_files} files processed). Finalizing graph structure...")
    extractor.finalize_graph()
    print(f"Extraction finished. Found {len(extractor.output_nodes)} nodes and {len(extractor.output_edges)} edges.")

    graph_structure = extractor.get_graph_data()

    if graph_structure["nodes"] or graph_structure["edges"]:
        try:
            graph_structure["nodes"].sort(key=lambda x: (x["node_type"], x["name"]))
            graph_structure["edges"].sort(key=lambda x: (x["source"], x["target"], x["condition"]))
            with open(output_filename, "w", encoding='utf-8') as f:
                json.dump(graph_structure, f, indent=2)
            print(f"Successfully wrote graph data to {output_filename}")
        except IOError as e:
            print(f"Error writing output file {output_filename}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"An unexpected error occurred during JSON serialization: {e}", file=sys.stderr)
    else:
        print("No graph structure extracted. Output file not written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", "-d", type=str, default=".", help="Target directory")
    parser.add_argument("--output", "-o", type=str, default="pydantic_ai_graph_output.json", help="Output file")
    args = parser.parse_args()

    extract_pydantic_ai_graph(args.directory, args.output)
