import argparse
import ast
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from enum import Enum
import logging
import re

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def sanitize_node_id(name: str) -> str:
    sanitized = re.sub(r'[^\w.-]', '_', name)
    if sanitized and (sanitized.startswith('.') or sanitized[0].isdigit()):
        sanitized = '_' + sanitized
    return sanitized if sanitized else "unnamed_node"

def get_potential_fqn(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        base = get_potential_fqn(node.value); return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Subscript): return get_potential_fqn(node.value)
    return None

def node_to_string(node: Optional[ast.AST]) -> str:
    if node is None: return "None"
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return get_potential_fqn(node) or "<Attribute>"
    if isinstance(node, ast.Constant): return repr(node.value)
    if isinstance(node, ast.Subscript):
        value_str = node_to_string(node.value); slice_node = node.slice
        slice_str = node_to_string(slice_node); return f"{value_str}[{slice_str}]"
    if isinstance(node, ast.Call):
        func_name = get_potential_fqn(node.func)
        args_str = ", ".join(node_to_string(arg) for arg in node.args)
        kwargs_str = ", ".join(f"{kw.arg}={node_to_string(kw.value)}" for kw in node.keywords)
        full_args = f"{args_str}{', ' if args_str and kwargs_str else ''}{kwargs_str}"
        return f"{func_name}({full_args})" if func_name else f"<Call({full_args})>"
    if isinstance(node, ast.List): return "[" + ", ".join(node_to_string(elt) for elt in node.elts) + "]"
    if isinstance(node, ast.Tuple): return "(" + ", ".join(node_to_string(elt) for elt in node.elts) + ")"
    if isinstance(node, ast.Dict): return "{" + ", ".join(f"{node_to_string(k)}: {node_to_string(v)}" for k, v in zip(node.keys, node.values)) + "}"
    if isinstance(node, ast.JoinedStr): return 'f"' + "".join(node_to_string(v) if isinstance(v, ast.FormattedValue) else str(getattr(v, 'value', '')) for v in node.values) + '"'
    try: return f"<ast.{type(node).__name__}>"
    except Exception: return "<unknown_node>"

def get_definition_location(node: ast.AST, filepath: Optional[str]) -> str:
    line = getattr(node, 'lineno', '?'); col = getattr(node, 'col_offset', '?')
    filepath_str = str(filepath) if filepath else "unknown_file"; return f"{filepath_str}:{line}:{col}"


class AdkNodeType(str, Enum):
    LLMAGENT = "LlmAgent"; SEQUENTIALAGENT = "SequentialAgent"; PARALLELAGENT = "ParallelAgent"
    LOOPAGENT = "LoopAgent"; CUSTOMAGENT = "CustomAgent"; BASEAGENT = "BaseAgent"
    TOOL_FUNCTION = "ToolFunction"; TOOL_AGENT = "ToolAgent"; UNKNOWN = "Unknown"
    START = "Start"; END = "End"

class AdkStructureExtractor(ast.NodeVisitor):
    def __init__(self):
        self.agent_instances: Dict[str, Dict[str, Any]] = {}
        self.custom_agent_classes: Dict[str, Dict[str, Any]] = {}
        self.all_functions: Dict[str, Dict[str, Any]] = {}
        self.imports_map: Dict[str, Dict[str, str]] = {}
        self.current_file_imports: Dict[str, str] = {}
        self.adk_classes: Dict[str, str] = {
            "BaseAgent": "google.adk.agents.BaseAgent", "LlmAgent": "google.adk.agents.LlmAgent",
            "Agent": "google.adk.agents.Agent", "SequentialAgent": "google.adk.agents.SequentialAgent",
            "ParallelAgent": "google.adk.agents.ParallelAgent", "LoopAgent": "google.adk.agents.LoopAgent",
            "AgentTool": "google.adk.tools.agent_tool.AgentTool",
            "FunctionTool": "google.adk.tools.FunctionTool",
        }
        self.local_adk_class_names: Dict[str, str] = {}
        self.current_filepath: Optional[str] = None

        self.output_nodes: List[Dict[str, Any]] = []
        self.output_edges: List[Dict[str, Any]] = []
        self.output_node_ids: Set[str] = set()
        self._node_id_counts: Dict[str, int] = {}
        self._tool_func_simple_name_to_node_id: Dict[str, str] = {}
        self._agent_var_to_node_id: Dict[str, str] = {}
        self._agent_tool_instance_map: Dict[str, str] = {}


    def _resolve_import(self, name: str, filepath: Optional[str]) -> Optional[str]:
        if not filepath or filepath not in self.imports_map: return name
        file_imports = self.imports_map[filepath]
        if name in file_imports: return file_imports[name]
        parts = name.split('.', 1)
        if len(parts) > 1 and parts[0] in file_imports:
             base_import = file_imports[parts[0]]
             if not base_import.endswith(parts[0]): return f"{base_import}.{parts[1]}"
             else: return f"{base_import}.{parts[1]}"
        return name

    def _get_value_repr(self, node: ast.AST, resolve_imports: bool = True) -> str:
        if isinstance(node, ast.Constant): return repr(node.value)
        if isinstance(node, ast.Name):
            lookup_dict = self.current_file_imports if resolve_imports else {}
            return lookup_dict.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base_repr = self._get_value_repr(node.value, resolve_imports=resolve_imports)
            return f"{base_repr}.{node.attr}"
        return node_to_string(node)

    def _get_call_kwargs(self, node: ast.Call) -> Dict[str, Any]:
        kwargs = {}
        function_tool_local_name = None
        for local, key in self.local_adk_class_names.items():
             if key == "FunctionTool":
                  function_tool_local_name = local
                  break
        if not function_tool_local_name:
             function_tool_local_name = "FunctionTool"

        for keyword in node.keywords:
            arg_name = keyword.arg
            if not arg_name: continue

            value_node = keyword.value

            if arg_name == "tools" and isinstance(value_node, ast.List):
                tool_reprs = []
                for elt in value_node.elts:
                    if isinstance(elt, ast.Call):
                        call_func_name = get_potential_fqn(elt.func)
                        if call_func_name == function_tool_local_name:
                            func_arg_value = None
                            for kw in elt.keywords:
                                if kw.arg == 'func':
                                    func_arg_value = kw.value
                                    break
                            if func_arg_value:
                                tool_reprs.append(self._get_value_repr(func_arg_value, resolve_imports=False))
                                logger.debug(f"Extracted tool '{tool_reprs[-1]}' from FunctionTool wrapper.")
                            else:
                                logger.warning(f"Found FunctionTool call without 'func' kwarg: {node_to_string(elt)}")
                                tool_reprs.append(f"<Invalid FunctionTool Call: {node_to_string(elt)}>")
                        else:
                            tool_reprs.append(self._get_value_repr(elt, resolve_imports=False))
                    else:
                        tool_reprs.append(self._get_value_repr(elt, resolve_imports=False))
                kwargs[arg_name] = tool_reprs
            elif arg_name != "tools":
                if isinstance(value_node, ast.Constant):
                    kwargs[arg_name] = value_node.value
                elif isinstance(value_node, ast.List):
                    kwargs[arg_name] = [self._get_value_repr(elt, resolve_imports=False) for elt in value_node.elts]
                else:
                    kwargs[arg_name] = self._get_value_repr(value_node, resolve_imports=False)

        return kwargs

    def _update_local_adk_names(self):
        self.local_adk_class_names.clear()
        imports_to_check = self.current_file_imports or {}
        for local_name, fqn_or_module in imports_to_check.items():
            for key, adk_fqn in self.adk_classes.items():
                if fqn_or_module == adk_fqn:
                    self.local_adk_class_names[local_name] = key
                    if key == "LlmAgent" and "Agent" in imports_to_check and imports_to_check["Agent"] == adk_fqn:
                         self.local_adk_class_names["Agent"] = "LlmAgent" # Handle alias
        for key in self.adk_classes:
             if key not in self.local_adk_class_names and key not in imports_to_check:
                 self.local_adk_class_names[key] = key


    def visit(self, node: ast.AST, filepath: Optional[str] = None):
        original_path = self.current_filepath
        original_imports = self.current_file_imports.copy()
        if filepath:
            self.current_filepath = filepath
            self.current_file_imports = self.imports_map.setdefault(filepath, {})
            self._update_local_adk_names()
        super().visit(node)
        self.current_filepath = original_path
        self.current_file_imports = original_imports
        if original_path: self._update_local_adk_names()

    def visit_Import(self, node: ast.Import):
        if not self.current_filepath: return
        for alias in node.names:
            module_name = alias.name; local_name = alias.asname or module_name
            self.current_file_imports[local_name] = module_name
        self._update_local_adk_names(); self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if not self.current_filepath or not node.module: return
        base_module = node.module
        for alias in node.names:
            imported_name = alias.name; local_name = alias.asname or imported_name
            full_path = f"{'.' * node.level}{base_module}.{imported_name}"
            self.current_file_imports[local_name] = full_path
        self._update_local_adk_names(); self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        class_name = node.name
        definition_loc = get_definition_location(node, self.current_filepath)
        base_classes_repr = [self._get_value_repr(b, resolve_imports=True) for b in node.bases]
        base_agent_fqn = self.adk_classes["BaseAgent"]
        if base_agent_fqn in base_classes_repr:
            logger.debug(f"Found Custom Agent Class: {class_name} at {definition_loc}")
            self.custom_agent_classes[class_name] = {"name": class_name, "bases": base_classes_repr, "definition_location": definition_loc, "node": node}
            self.local_adk_class_names[class_name] = "CustomAgent"
        original_class_name = getattr(self, 'current_class_name', None)
        self.current_class_name = class_name
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)): self.visit(item)
        self.current_class_name = original_class_name

    def visit_FunctionDef(self, node: ast.FunctionDef):
        func_name = node.name
        definition_loc = get_definition_location(node, self.current_filepath)
        docstring = ast.get_docstring(node)
        current_class = getattr(self, 'current_class_name', None)
        file_rel_path = os.path.relpath(self.current_filepath, ".") if self.current_filepath else "unknown"
        full_func_key = f"{file_rel_path}::{current_class}.{func_name}" if current_class else f"{file_rel_path}::{func_name}"
        simple_name = f"{current_class}.{func_name}" if current_class else func_name
        if full_func_key not in self.all_functions:
            logger.debug(f"Collected function/method definition: {simple_name} (key: {full_func_key}) at {definition_loc}")
            self.all_functions[full_func_key] = {
                "key": full_func_key, "simple_name": simple_name,
                "docstring": docstring, "definition_location": definition_loc,
                "node": node, "is_method": bool(current_class), "filepath": self.current_filepath
            }
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            instance_var_name = node.targets[0].id; value_node = node.value
            if isinstance(value_node, ast.Call):
                call_func_node = value_node.func
                local_name_used = get_potential_fqn(call_func_node)
                agent_type_key = self.local_adk_class_names.get(local_name_used) if local_name_used else None
                if agent_type_key and agent_type_key != "AgentTool":
                    kwargs = self._get_call_kwargs(value_node)
                    definition_loc = get_definition_location(node, self.current_filepath)
                    logger.debug(f"Found Agent Instance: {instance_var_name} (Type: {agent_type_key}) at {definition_loc}")
                    self.agent_instances[instance_var_name] = {
                        "instance_var_name": instance_var_name, "agent_type_key": agent_type_key,
                        "class_name_repr": self._get_value_repr(call_func_node, resolve_imports=False),
                        "name_param_repr": kwargs.get("name"), "description_param_repr": kwargs.get("description"),
                        "instruction_param_repr": kwargs.get("instruction"), "global_instruction_param_repr": kwargs.get("global_instruction"),
                        "model_param_repr": kwargs.get("model"),
                        "sub_agent_vars_repr": kwargs.get("sub_agents", []),
                        "tool_vars_repr": kwargs.get("tools", []),
                        "raw_kwargs": kwargs, "definition_location": definition_loc,
                        "filepath": self.current_filepath, "node": node
                    }
                elif agent_type_key == "AgentTool":
                     kwargs = self._get_call_kwargs(value_node)
                     wrapped_agent_var_repr = kwargs.get("agent")
                     tool_instance_name = instance_var_name
                     definition_loc = get_definition_location(node, self.current_filepath)
                     if wrapped_agent_var_repr and isinstance(wrapped_agent_var_repr, str):
                          logger.debug(f"Found AgentTool Instance: {tool_instance_name} wrapping '{wrapped_agent_var_repr}' at {definition_loc}")
                          self._agent_tool_instance_map[tool_instance_name] = wrapped_agent_var_repr
                     else: logger.warning(f"AgentTool instance {tool_instance_name} needs 'agent' kwarg.")
        self.generic_visit(node)

    def _generate_unique_id(self, base_id: str) -> str:
        sanitized_base = sanitize_node_id(base_id)
        count = self._node_id_counts.get(sanitized_base, 0) + 1
        self._node_id_counts[sanitized_base] = count
        return sanitized_base if count == 1 else f"{sanitized_base}_{count}"

    def _add_output_node(self, node_id: str, label: str, node_type: AdkNodeType, location: Optional[str] = None, metadata: Optional[Dict] = None):
        if node_id in self.output_node_ids: return
        self.output_nodes.append({
            "id": node_id, "name": label or node_id, "node_type": node_type.value,
            "location": location or "unknown", "metadata": metadata or {}
        })
        self.output_node_ids.add(node_id)

    def _add_output_edge(self, source_id: str, target_id: str, label: str, definition_location: Optional[str] = None, metadata: Optional[Dict] = None):
        if not (source_id in self.output_node_ids and target_id in self.output_node_ids):
            logger.warning(f"Skipping edge '{label}' from '{source_id}' to '{target_id}'. Source or target node ID not found.")
            return
        if any(e['source'] == source_id and e['target'] == target_id and e['label'] == label for e in self.output_edges):
            return
        edge_metadata = metadata or {};
        if definition_location: edge_metadata["definition_location"] = definition_location
        self.output_edges.append({"source": source_id, "target": target_id, "label": label, "metadata": edge_metadata})

    def _find_function_def(self, func_name_repr: str, agent_context_filepath: Optional[str]) -> Optional[Dict[str, Any]]:
        potential_match = next((data for data in self.all_functions.values() if data["simple_name"] == func_name_repr), None)
        if potential_match:
            return potential_match
        if agent_context_filepath:
            resolved_fqn = self._resolve_import(func_name_repr, agent_context_filepath)
            if resolved_fqn and resolved_fqn != func_name_repr:
                potential_match = next((data for data in self.all_functions.values() if data["simple_name"] == resolved_fqn or data["key"] == resolved_fqn), None)
                if potential_match: return potential_match
                potential_match = next((data for data in self.all_functions.values() if data["key"].endswith(f"::{resolved_fqn}")), None)
                if potential_match: return potential_match
        potential_match = self.all_functions.get(func_name_repr)
        if potential_match: return potential_match
        logger.warning(f"Could not find definition for function/tool '{func_name_repr}' referenced in context '{agent_context_filepath}'.")
        return None

    def finalize_graph(self):
        logger.info("Finalizing ADK graph structure...")
        self._node_id_counts.clear()
        self._agent_var_to_node_id.clear()
        self._tool_func_simple_name_to_node_id.clear()

        for agent_var, data in self.agent_instances.items():
            unique_node_id = self._generate_unique_id(agent_var)
            agent_type_key = data["agent_type_key"]
            node_type = AdkNodeType.LLMAGENT if agent_type_key in ["LlmAgent", "Agent"] \
                        else (AdkNodeType[agent_type_key.upper()] if agent_type_key != "CustomAgent"
                              else AdkNodeType.CUSTOM_AGENT)
            metadata = {
                "original_variable_name": agent_var, "class_called_repr": data["class_name_repr"],
                "name_parameter": data.get("name_param_repr"), "description": data.get("description_param_repr"),
                "instruction": data.get("instruction_param_repr"), "global_instruction": data.get("global_instruction_param_repr"),
                "model": data.get("model_param_repr"),
            }
            metadata = {k: v for k, v in metadata.items() if v is not None}
            self._add_output_node(unique_node_id, unique_node_id, node_type, data["definition_location"], metadata)
            self._agent_var_to_node_id[agent_var] = unique_node_id

        created_tool_node_ids = {}
        for agent_var, data in self.agent_instances.items():
            source_node_id = self._agent_var_to_node_id.get(agent_var)
            if not source_node_id: continue
            agent_definition_loc = data["definition_location"]
            agent_filepath = data["filepath"]
            agent_type_key = data["agent_type_key"]
            if agent_type_key not in ["LlmAgent", "Agent"]: continue

            for tool_var_repr in data.get("tool_vars_repr", []):
                target_node_id = None; edge_label = "uses_tool"; tool_metadata = {}

                if tool_var_repr in self._agent_tool_instance_map:
                    wrapped_agent_var = self._agent_tool_instance_map[tool_var_repr]
                    target_node_id = self._agent_var_to_node_id.get(wrapped_agent_var)
                    if target_node_id:
                        edge_label = "uses_tool (Agent)"; tool_metadata["via_agent_tool_instance"] = tool_var_repr
                    else: logger.warning(f"AgentTool '{tool_var_repr}' wraps unknown agent var '{wrapped_agent_var}'.")
                else:
                    func_data = self._find_function_def(tool_var_repr, agent_filepath)
                    if func_data:
                        simple_name = func_data["simple_name"]
                        if simple_name in created_tool_node_ids:
                            target_node_id = created_tool_node_ids[simple_name]
                        else:
                            target_node_id = self._generate_unique_id(simple_name)
                            tool_node_metadata = {
                                "original_function_key": func_data["key"], "simple_name": simple_name,
                                "docstring": func_data.get("docstring"), "is_method": func_data.get("is_method"),
                                "filepath": func_data.get("filepath"),
                            }
                            tool_node_metadata = {k: v for k, v in tool_node_metadata.items() if v is not None}
                            self._add_output_node(target_node_id, simple_name, AdkNodeType.TOOL_FUNCTION, func_data["definition_location"], tool_node_metadata)
                            created_tool_node_ids[simple_name] = target_node_id
                        edge_label = "uses_tool (Function)"

                if target_node_id:
                    self._add_output_edge(source_node_id, target_node_id, edge_label, agent_definition_loc, tool_metadata)

        for agent_var, data in self.agent_instances.items():
            source_node_id = self._agent_var_to_node_id.get(agent_var)
            if not source_node_id: continue
            agent_definition_loc = data["definition_location"]
            agent_type_key = data["agent_type_key"]
            node_type = AdkNodeType.LLMAGENT if agent_type_key in ["LlmAgent", "Agent"] \
                        else (AdkNodeType[agent_type_key.upper()] if agent_type_key != "CustomAgent" else AdkNodeType.CUSTOM_AGENT)
            for sub_agent_var_repr in data.get("sub_agent_vars_repr", []):
                target_node_id = self._agent_var_to_node_id.get(sub_agent_var_repr)
                if target_node_id:
                    edge_label = f"sub_agent ({node_type.value})"
                    self._add_output_edge(source_node_id, target_node_id, edge_label, agent_definition_loc)
                else: logger.warning(f"Sub-agent variable '{sub_agent_var_repr}' for agent '{agent_var}' not found.")

        all_sub_agent_vars = set()
        for data in self.agent_instances.values(): all_sub_agent_vars.update(data.get("sub_agent_vars_repr", []))
        root_agent_vars = set(self.agent_instances.keys()) - all_sub_agent_vars
        if self.output_nodes:
            start_node_id, end_node_id = "Start", "End"
            self._add_output_node(start_node_id, "Start", AdkNodeType.START, "system")
            self._add_output_node(end_node_id, "End", AdkNodeType.END, "system")
            if root_agent_vars:
                logger.info(f"Linking root agents to Start/End: {root_agent_vars}")
                for root_var in root_agent_vars:
                    root_node_id = self._agent_var_to_node_id.get(root_var)
                    if root_node_id:
                        self._add_output_edge(start_node_id, root_node_id, "initiates", "graph_analysis")
                        self._add_output_edge(root_node_id, end_node_id, "terminates (assumed)", "graph_analysis")
            else: logger.warning("No root agents identified to link Start/End.")

    def get_graph_data(self) -> Dict[str, List[Dict[str, Any]]]:
        return {"nodes": self.output_nodes, "edges": self.output_edges}

def extract_adk_graph(directory_path: str, output_filename: str):
    extractor = AdkStructureExtractor()
    logger.info(f"Starting Google ADK structure extraction in: {directory_path}")
    if not os.path.isdir(directory_path):
        logger.error(f"Provided path '{directory_path}' is not a valid directory."); return {"nodes": [], "edges": []}
    filepaths_to_parse = []
    for root, dirs, files in os.walk(directory_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', 'env', '__pycache__', 'node_modules', '.git']]
        for filename in files:
            if filename.endswith(".py"): filepaths_to_parse.append(os.path.join(root, filename))
    parsed_files = 0
    logger.info(f"Found {len(filepaths_to_parse)} Python files. Starting parsing pass...")
    for filepath in filepaths_to_parse:
        logger.debug(f"Parsing file: {filepath}")
        try:
            with open(filepath, "r", encoding='utf-8') as f: content = f.read()
            tree = ast.parse(content, filename=filepath)
            extractor.visit(tree, filepath=filepath)
            parsed_files += 1
        except SyntaxError as e: logger.warning(f"Skipping file {filepath} due to SyntaxError: {e}")
        except Exception as e: logger.error(f"Error parsing file {filepath}: {e}", exc_info=True)
    logger.info(f"AST parsing complete ({parsed_files} files processed). Finalizing graph structure...")
    extractor.finalize_graph()
    logger.info(f"Extraction finished. Found {len(extractor.output_nodes)} nodes and {len(extractor.output_edges)} edges.")
    graph_structure = extractor.get_graph_data()
    if graph_structure:
        graph_structure["metadata"] = {
            "framework": "GoogleADK",
             }
    if graph_structure["nodes"] or graph_structure["edges"]:
        try:
            graph_structure["nodes"].sort(key=lambda x: (x["name"], x["id"]))
            graph_structure["edges"].sort(key=lambda x: (x["source"], x["target"], x["label"]))
            with open(output_filename, "w", encoding='utf-8') as f: json.dump(graph_structure, f, indent=2)
            logger.info(f"Successfully wrote ADK graph data to {output_filename}")
        except IOError as e: logger.error(f"Error writing output file {output_filename}: {e}")
        except Exception as e: logger.error(f"An unexpected error occurred during JSON serialization: {e}")
    else: logger.info("No graph structure extracted. Output file not written.")
    return graph_structure

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse Google ADK Python code to extract agent graph structure.")
    parser.add_argument("--directory", "-d", type=str, default=".", help="Directory containing the ADK Python code.")
    parser.add_argument("--output", "-o", type=str, default="google_adk_graph_output.json", help="Output JSON file name.")
    args = parser.parse_args()
    if args.verbose: logging.getLogger().setLevel(logging.DEBUG)
    extract_adk_graph(args.directory, args.output)