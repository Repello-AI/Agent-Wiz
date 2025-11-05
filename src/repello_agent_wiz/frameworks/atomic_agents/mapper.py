import ast
import json
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pathlib import Path
from pydantic import BaseModel
from enum import Enum

class NodeType(str, Enum):
    AGENT = "Agent"
    TOOL = "Tool"
    CUSTOM_TOOL = "CustomTool"
    START = "Start"
    END = "End"

class ToolDefinition(BaseModel):
    name: str
    custom: bool
    description: Optional[str] = None

class AtomicAgentMapper(ast.NodeVisitor):
    DECORATOR_IDENTS = {"function_tool", "atomic.beta.tools.function_tool"}
    CONSTRUCTOR_IDENTS = {"AtomicAgent", "SearchTool", "FunctionTool"}
    TOOL_EXECUTE_PATTERNS = {"execute", "run_sync", "run_async", "run"}

    def __init__(self, filepath: str) -> None:
        super().__init__()
        self.current_filepath = filepath
        self.discovered_tools: dict[str, ToolDefinition] = {}
        self.tool_locations: dict[str, Dict] = {}
        self.agents: dict[str, Dict] = {}
        self.tool_calls: list[Dict] = []
    
    def visit_Assign(self, node: ast.Assign):
        if isinstance(node.value, ast.Call) and is_matching_call(node.value, self.CONSTRUCTOR_IDENTS):
            call_node = node.value
            constructor_name = get_identifier_string(call_node.func)

            if constructor_name == "AtomicAgent":
                self._process_atomic_agent_creation(node, call_node)
            else:
                self._process_tool_creation(node, call_node, constructor_name)

        self.generic_visit(node)
    
    def _process_atomic_agent_creation(self, node: ast.Assign, call_node: ast.Call):
        """Process AtomicAgent creation"""
        for target in node.targets:
            target_name = get_identifier_string(target)
            if target_name:
                location = create_location_info(node, self.current_filepath)
                config = self._extract_agent_config(call_node)

                tools_in_config = self._extract_tools_from_config(call_node)
                for tool_name in tools_in_config:
                    if tool_name not in self.discovered_tools:
                        tool_def = ToolDefinition(name=tool_name, custom=True, description=f"Tool used by {target_name}")
                        self.discovered_tools[tool_name] = tool_def
                        self.tool_locations[tool_name] = location

                agent_info = {
                    "name": target_name,
                    "type": "AtomicAgent",
                    "location": location,
                    "config": config,
                    "tools": tools_in_config
                }
                self.agents[target_name] = agent_info
    
    def _process_tool_creation(self, node: ast.Assign, call_node: ast.Call, constructor_name: str):
        """Process tool creation (SearchTool, FunctionTool, etc.)"""
        tool_name = extract_kwarg_string(call_node, "name")
        if not tool_name:
            for target in node.targets:
                target_name = get_identifier_string(target)
                if target_name:
                    tool_name = target_name
                    break

        if tool_name:
            description = extract_kwarg_string(call_node, "description") or ""
            location = create_location_info(node, self.current_filepath)
            tool_def = ToolDefinition(name=tool_name, custom=True, description=description)

            for target in node.targets:
                target_name = get_identifier_string(target)
                if target_name:
                    self.discovered_tools[target_name] = tool_def
                    if location: self.tool_locations[target_name] = location
    
    def _extract_agent_config(self, call_node: ast.Call) -> Dict:
        """Extract configuration from AtomicAgent call"""
        config = {}
        for kw in call_node.keywords:
            if kw.arg == "config" and isinstance(kw.value, ast.Call):
                config_node = kw.value
                for config_kw in config_node.keywords:
                    if config_kw.arg:
                        config[config_kw.arg] = represent_node(config_kw.value)
        return config

    def _extract_tools_from_config(self, call_node: ast.Call) -> List[str]:
        """Extract tool names from AgentConfig"""
        tool_names = []
        for kw in call_node.keywords:
            if kw.arg == "config" and isinstance(kw.value, ast.Call):
                config_node = kw.value
                for config_kw in config_node.keywords:
                    if config_kw.arg == "tools" and isinstance(config_kw.value, ast.List):
                        for tool_node in config_kw.value.elts:
                            tool_name = get_identifier_string(tool_node)
                            if tool_name:
                                tool_names.append(tool_name)
        return tool_names

    def visit_ClassDef(self, node: ast.ClassDef):
        """Detect tool class definitions like SearchTool"""
        class_name = node.name
        if self._is_tool_class(node):
            location = create_location_info(node, self.current_filepath)
            tool_def = ToolDefinition(name=class_name, custom=True, description=ast.get_docstring(node) or "")
            self.discovered_tools[class_name] = tool_def
            if location: self.tool_locations[class_name] = location

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """Detect tool execution patterns like tool.execute() and agent.run_sync()"""
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            if method_name in self.TOOL_EXECUTE_PATTERNS:
                obj_name = get_identifier_string(node.func.value)
                if obj_name:
                    location = create_location_info(node, self.current_filepath)
                    call_info = {
                        "object": obj_name,
                        "method": method_name,
                        "location": location,
                        "args": [represent_node(arg) for arg in node.args]
                    }
                    self.tool_calls.append(call_info)

        self.generic_visit(node)

    def _is_tool_class(self, node: ast.ClassDef) -> bool:
        """Check if a class is a tool class"""
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "execute":
                return True
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "input_schema":
                        return True
        return False

    def visit_FunctionDef(self, node): self._process_function(node)
    def visit_AsyncFunctionDef(self, node): self._process_function(node)

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

def find_decorator_node(node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef], target_names: Set[str]) -> Optional[ast.AST]:
    for decorator in node.decorator_list:
        deco_node_to_check = decorator
        if isinstance(decorator, ast.Call):
            deco_node_to_check = decorator.func 
        deco_name = get_identifier_string(deco_node_to_check)
        if deco_name and deco_name in target_names:
            return decorator 
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

def get_kwarg_node(call_node: ast.Call, keyword: str) -> Optional[ast.AST]:
    if not isinstance(call_node, ast.Call): raise TypeError("Expected an ast.Call node")
    for kw in call_node.keywords:
        if kw.arg == keyword: return kw.value
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

def build_graph_json(
    agents: dict[str, Dict],
    custom_tools: dict[str, ToolDefinition],
    custom_tool_locs: dict[str, Dict],
    tool_calls: list[Dict]
) -> Dict[str, List[Dict]]:
    """Build graph structure from extracted atomic agents data"""
    nodes: List[Dict] = []
    edges: List[Dict] = []
    processed_ids: Set[str] = set()
 
    for agent_name, agent_info in agents.items():
        if agent_name in processed_ids:
            continue
        
        node_meta = {
            "type": agent_info.get("type"),
            "config": agent_info.get("config", {})
        }
        node_meta = {k: v for k, v in node_meta.items() if v is not None and v != {}}
        
        node = {
            "id": agent_name,
            "name": agent_name,
            "node_type": NodeType.AGENT.value,
            "source_location": agent_info.get("location"),
            "metadata": node_meta
        }
        nodes.append(node)
        processed_ids.add(agent_name)

    tool_refs = set()
    for call in tool_calls:
        obj_name = call.get("object")
        if obj_name and obj_name in custom_tools:
            tool_refs.add(obj_name)
 
    for agent_info in agents.values():
        agent_tools = agent_info.get("tools", [])
        for tool_name in agent_tools:
            if tool_name in custom_tools:
                tool_refs.add(tool_name)
 
    for tool_id in tool_refs:
        if tool_id in processed_ids:
            continue
        
        tool_def = custom_tools.get(tool_id)
        if not tool_def:
            print(f"Warning: Tool '{tool_id}' referenced but definition missing. Skipping node.")
            continue

        location = custom_tool_locs.get(tool_id)
        tool_node = {
            "id": tool_id,
            "name": tool_id,
            "function_name": tool_id,
            "docstring": tool_def.description,
            "node_type": NodeType.CUSTOM_TOOL.value,
            "source_location": location,
            "metadata": {"custom": True}
        }
        nodes.append(tool_node)
        processed_ids.add(tool_id)
 
    for call in tool_calls:
        obj_name = call.get("object")
        method = call.get("method")
        location = call.get("location")
        
        if obj_name in agents:
            source_id = "Start"
            target_id = obj_name
            edges.append({
                "source": source_id,
                "target": target_id,
                "edge_type": "execution_start",
                "condition": {},
                "metadata": {
                    "method": method,
                    "definition_location": location
                }
            })
        elif obj_name in custom_tools: 
            pass  

    for agent_name, agent_info in agents.items():
        agent_tools = agent_info.get("tools", [])
        agent_loc = agent_info.get("location")
        for tool_name in agent_tools:
            if tool_name in processed_ids:
                edges.append({
                    "source": agent_name,
                    "target": tool_name,
                    "edge_type": "tool_usage",
                    "condition": {},
                    "metadata": {
                        "definition_location": agent_loc
                    }
                })
    for call in tool_calls:
        obj_name = call.get("object")
        location = call.get("location")

        if obj_name in custom_tools and obj_name in processed_ids:
            for agent_name in agents.keys():
                agent_loc = agents[agent_name].get("location")
                if agent_loc and location and agent_loc.get("file") == location.get("file"):
                    edges.append({
                        "source": agent_name,
                        "target": obj_name,
                        "edge_type": "tool_usage",
                        "condition": {},
                        "metadata": {
                            "method": call.get("method"),
                            "definition_location": location
                        }
                    })
                    break  
    start_id, end_id = "Start", "End"
    if start_id not in processed_ids:
        nodes.append({"id": "Start", "name": "Start", "node_type": NodeType.START.value})
        processed_ids.add(start_id)
    if end_id not in processed_ids:
        nodes.append({"id": "End", "name": "End", "node_type": NodeType.END.value})
        processed_ids.add(end_id)

    agent_ids = set(agents.keys())
    incoming: Dict[str, int] = {name: 0 for name in agent_ids}
    outgoing: Dict[str, int] = {name: 0 for name in agent_ids}
    
    for edge in edges:
        if edge.get("source") in agent_ids:
            outgoing[edge["source"]] = outgoing.get(edge["source"], 0) + 1
        if edge.get("target") in agent_ids:
            incoming[edge["target"]] = incoming.get(edge["target"], 0) + 1

    for agent_id in agent_ids:
        if incoming.get(agent_id, 0) == 0:
            if not any(e["source"] == start_id and e["target"] == agent_id for e in edges):
                edges.append({
                    "source": start_id,
                    "target": agent_id,
                    "edge_type": "implicit_start",
                    "condition": {},
                    "metadata": {}
                })

    for agent_id in agent_ids:
        if not any(e["source"] == agent_id and e["target"] == end_id for e in edges):
            edges.append({
                "source": agent_id,
                "target": end_id,
                "edge_type": "implicit_end",
                "condition": {},
                "metadata": {}
            })

    tool_node_ids = [node["id"] for node in nodes if node.get("node_type") == NodeType.CUSTOM_TOOL.value]
    for tool_id in tool_node_ids:
        if not any(e["source"] == tool_id and e["target"] == end_id for e in edges):
            edges.append({
                "source": tool_id,
                "target": end_id,
                "edge_type": "implicit_end",
                "condition": {},
                "metadata": {}
            })

    for node in nodes:
        node.setdefault("function_name", None)
        node.setdefault("docstring", None)
        node.setdefault("source_location", None)
        node.setdefault("metadata", {})

    for node in nodes:
        if "metadata" in node:
            node["metadata"] = {k: v for k, v in node["metadata"].items() if v is not None}

    return {"nodes": nodes, "edges": edges}


def extract_atomic_agents_graph(scan_path: str, output_file: str):
    """Extract and save atomic agents graph structure to JSON""" 
    if not os.path.isdir(scan_path):
        print(f"Error: Path '{scan_path}' is not a valid directory.")
        sys.exit(1) 

    try:
        custom_tools, custom_locs, agents, tool_calls = gather_tool_definitions(scan_path)

        final_graph = build_graph_json(
            agents=agents,
            custom_tools=custom_tools,
            custom_tool_locs=custom_locs,
            tool_calls=tool_calls
        ) 

        if final_graph:
            final_graph["metadata"] = {
                "framework": "Atomic_Agents",
            }
    
    except Exception as e:
        print(f"Error during graph extraction: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if final_graph["nodes"] or final_graph["edges"]:
        try:
            final_graph["nodes"].sort(key=lambda x: x.get('id', ''))
            final_graph["edges"].sort(key=lambda x: (x.get('source', ''), x.get('target', ''), x.get('edge_type', '')))

            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding='utf-8') as f:
                json.dump(final_graph, f, indent=2)
            print(f"Graph written to {output_file}") 

            if not final_graph["nodes"]:
                print("No nodes found.")
            for node in final_graph["nodes"]:
                loc = node.get('source_location')
                loc_str = f" ({loc['file']}:{loc['line']})" if loc and loc.get('file') else ""
                node_id = node.get('id', 'Unknown ID')
                node_type = node.get('node_type', 'Unknown Type') 

            if not final_graph["edges"]:
                print("No edges found.")
            for edge in final_graph["edges"]:
                meta_loc = edge.get('metadata', {}).get('definition_location')
 
        except Exception as e:
            print(f"Error writing JSON output or printing summary: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        print("\nNo graph structure found or extracted.")
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding='utf-8') as f:
                json.dump({"nodes": [], "edges": [], "metadata": {"framework": "Atomic_Agents"}}, f, indent=2)
            print(f"Graph written to {output_file}")
        except Exception as e:
            print(f"Error writing empty JSON output to {output_path}: {e}")
            sys.exit(1)


def gather_tool_definitions(root_path: str) -> Tuple[dict[str, ToolDefinition], dict[str, Dict], dict[str, Dict], list[Dict]]:
    all_tools: dict[str, ToolDefinition] = {}
    all_locations: dict[str, Dict] = {}
    all_agents: dict[str, Dict] = {}
    all_tool_calls: list[Dict] = []
    
    for file_path in Path(root_path).rglob("*.py"):
        filepath_str = str(file_path)
        filename = file_path.name
        print(f"Processing file: {filename}")
        try:
            with open(file_path, "r", encoding='utf-8') as f:
                content = f.read()
                tree = ast.parse(content, filename=filepath_str)
                extractor = AtomicAgentMapper(filepath_str)
                extractor.visit(tree)

                all_tools.update(extractor.discovered_tools)
                all_locations.update(extractor.tool_locations)
                all_agents.update(extractor.agents)
                all_tool_calls.extend(extractor.tool_calls)
        except SyntaxError as e: print(f"Warning: Skipping file {filepath_str} due to SyntaxError: {e}")
        except Exception as e: print(f"Warning: Skipping file {filepath_str} due to unexpected error: {e}") 

    return all_tools, all_locations, all_agents, all_tool_calls
