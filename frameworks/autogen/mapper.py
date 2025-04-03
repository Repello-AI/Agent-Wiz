import os
import ast
import sys
import json
import argparse


class AutoGenMapper(ast.NodeVisitor):
    
    def __init__(self, filename: str | None = None):
        self.current_filename = filename
        self.agents = {}
        self.tools = {}
        self.functions = {}
        self.tool_calls = {}
        self.agent_tool_func_map = {}

        self.known_agent_bases = {"Agent", "RoutedAgent"}
        self.known_tool_wrappers = {"Tool", "FunctionTool"}


    def visit_Import(self, node: ast.Import):
        """
        Visit an Import node and extract relevant information.
        """
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """
        Visit an Import node and extract relevant information.
        """
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        if isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    tool_list = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Call):
                            if (
                                isinstance(elt.func, ast.Name)
                                and elt.func.id in self.known_tool_wrappers
                            ):
                                if elt.args:
                                    func_node = elt.args[0]
                                    if isinstance(func_node, ast.Name):
                                        tool_list.append(func_node.id)

                    if tool_list:
                        print(f"Found tool list: {target.id} → {tool_list}")
                        self.tools[target.id] = tool_list

        self.generic_visit(node)


    def visit_ClassDef(self, node: ast.ClassDef):
        base_names = [
            base.id for base in node.bases 
            if isinstance(base, ast.Name) and base.id in self.known_agent_bases
        ]

        if base_names:
            agent_info = {
                "type": "agent",
                "tools": [],
                "inherits": base_names
            }

            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    for stmt in item.body:
                        if isinstance(stmt, ast.Assign):
                            for target in stmt.targets:
                                if (isinstance(target, ast.Attribute) and target.attr == "_tools"):
                                    if isinstance(stmt.value, ast.Name):
                                        agent_info["tools"].append(stmt.value.id)

            self.agents[node.name] = agent_info

        self.generic_visit(node)


    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._register_function_metadata(node)
        self._track_tool_calls(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._register_function_metadata(node)
        self._track_tool_calls(node)
        self.generic_visit(node)


    def _register_function_metadata(self, node):
        self.functions[node.name] = {
            "docstring": ast.get_docstring(node),
            "source_location": {
                "file": self.current_filename,
                "line": node.lineno,
                "col": node.col_offset,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "end_col": getattr(node, "end_col_offset", node.col_offset)
            }
        }

    def _track_tool_calls(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        current_func = node.name
        called_funcs = set()

        class CallVisitor(ast.NodeVisitor):
            def visit_Call(self, call_node: ast.Call):
                if isinstance(call_node.func, ast.Name):
                    called_funcs.add(call_node.func.id)
                self.generic_visit(call_node)

        CallVisitor().visit(node)

        if called_funcs:
            self.tool_calls[str(current_func)] = list(called_funcs)


    def visit_Await(self, node: ast.Await):
        # Look for calls like: await SomeAgent.register(...)
        if isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "register":
                if len(call.args) >= 3:
                    agent_class = self._get_id(call.func.value)
                    agent_name = self._get_str(call.args[1])
                    lambda_fn = call.args[2]
                    
                    if isinstance(lambda_fn, ast.Lambda):
                        if isinstance(lambda_fn.body, ast.Call):
                            tool_arg = lambda_fn.body.args[-1]
                            if isinstance(tool_arg, ast.Name):
                                tool_var = tool_arg.id
                                print(f"Registered {agent_name} -> {agent_class} using tools: {tool_var}")
                                self.agents.setdefault(agent_class, {
                                    "type": "agent",
                                    "tools": [],
                                    "inherits": []
                                })
                                self.agents[agent_class]["tools"].append(tool_var)
        
        self.generic_visit(node)

    def _get_id(self, node):
        return node.id if isinstance(node, ast.Name) else None

    def _get_str(self, node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _read_file(file_path: str) -> str:
    """
    Reads the content of a file and returns it as a string.
    """

    try:
        with open(file_path, "r", encoding='utf-8') as file:
            return file.read()
    
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return ""

def build_graph_data(visitor: AutoGenMapper):
    nodes = []
    edges = []

    # Per-visitor: Add __start__ and __end__
    nodes.append({
        "name": "__start__",
        "function_name": None,
        "docstring": None,
        "node_type": "Special",
        "source_location": None,
        "metadata": {}
    })
    nodes.append({
        "name": "__end__",
        "function_name": None,
        "docstring": None,
        "node_type": "Special",
        "source_location": None,
        "metadata": {}
    })

    for agent_name, agent_data in visitor.agents.items():
        # Agent node
        nodes.append({
            "name": agent_name,
            "function_name": "Assistant",
            "docstring": None,
            "node_type": "Generic",
            "source_location": None,
            "metadata": {}
        })

        # ⬅️ These stay inside build_graph_data to avoid merging
        edges.append({
            "source": "__start__",
            "target": agent_name,
            "condition": { "type": "static" },
            "metadata": { "reason": "per-visitor entry point" }
        })
        edges.append({
            "source": agent_name,
            "target": "__end__",
            "condition": { "type": "static" },
            "metadata": { "reason": "per-visitor exit point" }
        })

        # Tool nodes and edges
        for tool_var in agent_data["tools"]:
            tool_funcs = visitor.tools.get(tool_var, [])
            for func_name in tool_funcs:
                tool_meta = visitor.functions.get(func_name, {})
                tool_node = {
                    "name": f"{agent_name}_{func_name}",
                    "function_name": func_name,
                    "docstring": tool_meta.get("docstring"),
                    "node_type": "Tool",
                    "source_location": tool_meta.get("source_location"),
                    "metadata": {}
                }
                nodes.append(tool_node)

                edges.append({
                    "source": agent_name,
                    "target": tool_node["name"],
                    "condition": { "type": "static" },
                    "metadata": {}
                })

    # Tool-to-tool edges (unchanged)
    all_tools = [f for funcs in visitor.tools.values() for f in funcs]

    for source_func, called_funcs in visitor.tool_calls.items():
        if source_func not in all_tools:
            continue
        for target_func in called_funcs:
            if target_func in all_tools:
                for agent_name, agent_data in visitor.agents.items():
                    for tool_var in agent_data.get("tools", []):
                        if (
                            source_func in visitor.tools.get(tool_var, []) and
                            target_func in visitor.tools.get(tool_var, [])
                        ):
                            edges.append({
                                "source": f"{agent_name}_{source_func}",
                                "target": f"{agent_name}_{target_func}",
                                "condition": { "type": "internal_call" },
                                "metadata": { "reason": f"{source_func} calls {target_func}" }
                            })

    return {
        "nodes": nodes,
        "edges": edges
    }



def merge_visitors(visitors: list[AutoGenMapper]) -> AutoGenMapper:
    merged = AutoGenMapper()

    for visitor in visitors:
        # Merge agents deeply
        for agent_name, agent_data in visitor.agents.items():
            if agent_name not in merged.agents:
                merged.agents[agent_name] = {
                    "type": agent_data["type"],
                    "tools": list(agent_data["tools"]),
                    "inherits": list(agent_data["inherits"]),
                }
            else:
                merged.agents[agent_name]["tools"].extend(
                    t for t in agent_data["tools"] if t not in merged.agents[agent_name]["tools"]
                )
                merged.agents[agent_name]["inherits"].extend(
                    i for i in agent_data["inherits"] if i not in merged.agents[agent_name]["inherits"]
                )

        # Merge tools (tool var → list of function names)
        for tool_var, tool_funcs in visitor.tools.items():
            if tool_var not in merged.tools:
                merged.tools[tool_var] = list(tool_funcs)
            else:
                merged.tools[tool_var].extend(
                    func for func in tool_funcs if func not in merged.tools[tool_var]
                )

        # Merge function definitions (last one wins, safe unless exact name collision)
        merged.functions.update(visitor.functions)

        # Merge tool calls
        for func, called in visitor.tool_calls.items():
            if func not in merged.tool_calls:
                merged.tool_calls[func] = list(called)
            else:
                merged.tool_calls[func].extend(
                    c for c in called if c not in merged.tool_calls[func]
                )

    return merged



def extract_autogen_graph_from_code(target_directory: str, output_filename: str):
    """
    Extracts the AutoGen agentic graph structure from a python project and saves it as a JSON file.
    """

    visitors = []

    # Process each Python file
    for root, _, files in os.walk(target_directory):
        for filename in files:

            # Check if the file is a Python file
            if filename.endswith(".py"):
                filepath = os.path.join(root, filename)

                file_content = _read_file(filepath)

                if not file_content:
                    continue
                
                # Parse the file content into an AST & create a visitor
                try:
                    tree = ast.parse(file_content)

                    visitor = AutoGenMapper(filepath)
                    
                    # Visit the AST nodes
                    visitor.visit(tree)
                    visitors.append(visitor)
                
                except SyntaxError as e:
                    print(f"Syntax error in file {filepath}: {e}")
                    continue
    
    merged_visitor = merge_visitors(visitors)
    graph_data = build_graph_data(merged_visitor)

    # Write to output file
    try:
        with open(output_filename, "w", encoding='utf-8') as outfile:
            json.dump(graph_data, outfile, indent=4)  # Pretty print
        print(f"AutoGen graph data written to {output_filename}")
    except Exception as e:
        print(f"Error writing JSON output to {output_filename}: {e}")
        sys.exit(1)
    
    # Success exit
    sys.exit(0)


# --- Main Execution Block ---
if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Extract AutoGen structure from Python files into a LangGraph-compatible format.")
    parser.add_argument("--directory", "-d", type=str, default=".", help="Directory to search for Python files")
    parser.add_argument("--output", "-o", type=str, default="graph_data.json", help="Output JSON file path")
    
    args = parser.parse_args()
    
    target_directory = args.directory
    output_filename = args.output

    extract_autogen_graph_from_code(target_directory, output_filename)

