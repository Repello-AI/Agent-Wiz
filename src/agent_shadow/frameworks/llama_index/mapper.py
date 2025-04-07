import ast
import os
import sys
import json


class LlamaIndexMapper(ast.NodeVisitor):
    def __init__(self, filename=None):
        self.current_filename = filename
        self.agents = {}
        self.tools = {}
        self.functions = {}
        self.tool_calls = {}
        self.agent_handoffs = []

    def visit_Assign(self, node):
        if isinstance(node.value, ast.Call) and hasattr(node.value.func, 'id'):
            func_name = node.value.func.id

            if func_name in {"FunctionAgent", "ReActAgent"}:
                kwargs = {kw.arg: kw.value for kw in node.value.keywords}
                tools = kwargs.get("tools")
                agent_name = None

                # Try to infer agent name
                if func_name == "FunctionAgent":
                    name_kwarg = kwargs.get("name")
                    if isinstance(name_kwarg, ast.Constant):
                        agent_name = name_kwarg.value
                else:  # ReActAgent fallback
                    agent_name = node.targets[0].id if node.targets and isinstance(node.targets[0], ast.Name) else f"ReActAgent_{node.lineno}"

                if agent_name:
                    self.agents[agent_name] = {
                        "type": "agent",
                        "tools": [],
                        "inherits": [func_name],
                        "handoffs": []
                    }

                    if isinstance(tools, ast.List):
                        for elt in tools.elts:
                            if isinstance(elt, ast.Name):
                                tool_name = elt.id
                                self.agents[agent_name]["tools"].append(tool_name)
                                self.tools[tool_name] = [tool_name]  # Map tool_var → func

                    if func_name == "FunctionAgent":
                        handoffs = kwargs.get("can_handoff_to")
                        if isinstance(handoffs, ast.List):
                            for elt in handoffs.elts:
                                if isinstance(elt, ast.Constant):
                                    self.agents[agent_name]["handoffs"].append(elt.value)
                                    self.agent_handoffs.append((agent_name, elt.value))

        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._register_function_metadata(node)
        self._track_tool_calls(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
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

    def _track_tool_calls(self, node):
        current_func = node.name
        called_funcs = set()

        class CallVisitor(ast.NodeVisitor):
            def visit_Call(self, call_node):
                if isinstance(call_node.func, ast.Name):
                    called_funcs.add(call_node.func.id)
                self.generic_visit(call_node)

        CallVisitor().visit(node)
        if called_funcs:
            self.tool_calls[current_func] = list(called_funcs)


def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return ""


def build_graph_data(visitor):
    nodes = []
    edges = []

    nodes.append({"name": "__start__", "function_name": None, "node_type": "Special", "docstring": None, "source_location": None, "metadata": {}})
    nodes.append({"name": "__end__", "function_name": None, "node_type": "Special", "docstring": None, "source_location": None, "metadata": {}})

    for agent_name, agent_data in visitor.agents.items():
        nodes.append({
            "name": agent_name,
            "function_name": "Assistant",
            "docstring": None,
            "node_type": "Agent",
            "source_location": None,
            "metadata": {
                "inherits": agent_data.get("inherits", [])
            }
        })

        edges.append({"source": "__start__", "target": agent_name, "condition": {"type": "static"}, "metadata": {}})
        edges.append({"source": agent_name, "target": "__end__", "condition": {"type": "static"}, "metadata": {}})

        for tool in agent_data["tools"]:
            tool_meta = visitor.functions.get(tool, {})
            tool_node_name = f"{agent_name}_{tool}"
            nodes.append({
                "name": tool_node_name,
                "function_name": tool,
                "docstring": tool_meta.get("docstring"),
                "node_type": "Tool",
                "source_location": tool_meta.get("source_location"),
                "metadata": {}
            })
            edges.append({
                "source": agent_name,
                "target": tool_node_name,
                "condition": {"type": "static"},
                "metadata": {}
            })

    for source_func, targets in visitor.tool_calls.items():
        for target_func in targets:
            for agent_name, agent_data in visitor.agents.items():
                if source_func in agent_data["tools"] and target_func in agent_data["tools"]:
                    edges.append({
                        "source": f"{agent_name}_{source_func}",
                        "target": f"{agent_name}_{target_func}",
                        "condition": {"type": "internal_call"},
                        "metadata": {}
                    })

    for source, target in visitor.agent_handoffs:
        edges.append({
            "source": source,
            "target": target,
            "condition": {"type": "handoff"},
            "metadata": {}
        })

    return {"nodes": nodes, "edges": edges}


def extract_llamaindex_graph(directory, output_file):
    visitors = []
    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.endswith(".py"):
                filepath = os.path.join(root, filename)
                content = _read_file(filepath)
                if not content:
                    continue
                try:
                    tree = ast.parse(content)
                    visitor = LlamaIndexMapper(filepath)
                    visitor.visit(tree)
                    visitors.append(visitor)
                except SyntaxError as e:
                    print(f"Syntax error in {filepath}: {e}")
                    continue

    
    graph_data = build_graph_data(visitor)

    if graph_data:
        graph_data["metadata"] = {
            "framework": "LlamaIndex",
        }

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, indent=4)
        print(f"Graph saved to {output_file}")
    except Exception as e:
        print(f"Error writing file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", "-d", type=str, default=".", help="Source directory")
    parser.add_argument("--output", "-o", type=str, default="llamaindex_graph.json", help="Output file")
    args = parser.parse_args()

    extract_llamaindex_graph(args.directory, args.output)
