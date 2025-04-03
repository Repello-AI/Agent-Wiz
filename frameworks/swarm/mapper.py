import ast
import os
import json
import sys


class SwarmMapper(ast.NodeVisitor):
    def __init__(self, filename=None):
        self.current_filename = filename
        self.agents = {}
        self.tools = {}
        self.functions = {}
        self.agent_routing_edges = []

        self.function_returns = {}  # function_name → returned variable
        self.tool_function_to_agent = {}  # func → agent_var (built after parsing)


    def visit_Assign(self, node):
        if isinstance(node.value, ast.Call) and hasattr(node.value.func, 'id'):
            func_id = node.value.func.id

            # Detect Agent(...)
            if func_id == "Agent":
                kwargs = {kw.arg: kw.value for kw in node.value.keywords}
                name = self._get_constant(kwargs.get("name"))
                functions = kwargs.get("functions", [])

                if name:
                    agent_var = node.targets[0].id if isinstance(node.targets[0], ast.Name) else name
                    
                    if agent_var:
                        self.current_agent_var = agent_var
                    self.agents[name] = {
                        "var": agent_var,
                        "type": "Agent",
                        "tools": self._extract_tool_names(functions),
                        "inherits": ["Agent"]
                    }
                

            # Detect Triage Agent
            elif func_id == "create_triage_agent":
                kwargs = {kw.arg: kw.value for kw in node.value.keywords}
                name = self._get_constant(kwargs.get("name"))
                agents_list = kwargs.get("agents")

                triage_var = node.targets[0].id if isinstance(node.targets[0], ast.Name) else name
                self.agents[name] = {
                    "var": triage_var,
                    "type": "TriageAgent",
                    "tools": [],
                    "inherits": ["create_triage_agent"]
                }

                if isinstance(agents_list, ast.List):
                    for agent in agents_list.elts:
                        if isinstance(agent, ast.Name):
                            target_var = agent.id
                            self.agent_routing_edges.append((name, target_var))

        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._register_function_metadata(node)

        returned_var = None
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name):
                returned_var = stmt.value.id

        if returned_var:
            self.function_returns[node.name] = returned_var

        self.generic_visit(node)


    def visit_AsyncFunctionDef(self, node):
        self._register_function_metadata(node)

    def _get_constant(self, node):
        return node.value if isinstance(node, ast.Constant) else None

    def _extract_tool_names(self, tool_list_node):
        tools = []
        if isinstance(tool_list_node, ast.List):
            for elt in tool_list_node.elts:
                if isinstance(elt, ast.Name):
                    tools.append(elt.id)
        return tools

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


def build_graph_data(visitor):
    nodes = []
    edges = []

    for agent_name, agent_data in visitor.agents.items():
        nodes.append({
            "name": agent_name,
            "function_name": "Assistant",
            "docstring": None,
            "node_type": "Generic",
            "source_location": None,
            "metadata": {"inherits": agent_data["inherits"]}
        })

        edges.append({"source": "__start__", "target": agent_name, "condition": {"type": "static"}, "metadata": {}})
        edges.append({"source": agent_name, "target": "__end__", "condition": {"type": "static"}, "metadata": {}})

        for func_name in agent_data["tools"]:
            tool_meta = visitor.functions.get(func_name, {})
            tool_node_name = f"{agent_name}_{func_name}"

            nodes.append({
                "name": tool_node_name,
                "function_name": func_name,
                "docstring": tool_meta.get("docstring"),
                "node_type": "Tool",
                "source_location": tool_meta.get("source_location"),
                "metadata": {}
            })

            # Only add static Agent → Tool edge if NOT already used in routing
            is_used_in_routing = any(
                len(route) == 3 and route[0] == agent_name and route[2] == func_name
                for route in visitor.agent_routing_edges
            )

            if not is_used_in_routing:
                edges.append({
                    "source": agent_name,
                    "target": tool_node_name,
                    "condition": {"type": "static"},
                    "metadata": {}
                })

    # Build reverse mapping from var name → agent display name
    var_to_agent_name = {v["var"]: k for k, v in visitor.agents.items()}

    for routing in visitor.agent_routing_edges:
        if len(routing) == 3:
            source_agent, target_agent_name, via_func = routing
            intermediate_tool_node = f"{source_agent}_{via_func}"

            edges.append({
                "source": source_agent,
                "target": intermediate_tool_node,
                "condition": {"type": "static"},
                "metadata": {}
            })

            edges.append({
                "source": intermediate_tool_node,
                "target": target_agent_name,
                "condition": {"type": "route"},
                "metadata": {"via": "function_return"}
            })

        elif len(routing) == 2:
            # Legacy route, still allowed (e.g. create_triage_agent)
            source_agent, target_var = routing
            target_agent_name = var_to_agent_name.get(target_var, target_var)

            edges.append({
                "source": source_agent,
                "target": target_agent_name,
                "condition": {"type": "route"},
                "metadata": {"via": "create_triage_agent"}
            })

    return {"nodes": nodes, "edges": edges}



def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"Failed to read {path}: {e}")
        return ""


def extract_swarm_graph(directory, output_file):
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
                    visitor = SwarmMapper(filepath)
                    visitor.visit(tree)
                    visitors.append(visitor)
                except SyntaxError as e:
                    print(f"Syntax error in {filepath}: {e}")
                    continue

    # After visitors are collected
    # Build agent var map: var_name → display_name
    agent_var_to_name = {}
    for visitor in visitors:
        for name, agent in visitor.agents.items():
            agent_var_to_name[agent["var"]] = name
            # Reverse map: tool func → agent_var
            for func in agent["tools"]:
                visitor.tool_function_to_agent[func] = agent["var"]

    # Now resolve function returns to agents
    for visitor in visitors:
        for func, returned_var in visitor.function_returns.items():
            source_agent_var = visitor.tool_function_to_agent.get(func)
            target_agent_name = agent_var_to_name.get(returned_var)
            source_agent_name = agent_var_to_name.get(source_agent_var)

            if source_agent_name and target_agent_name:
                visitor.agent_routing_edges.append(
                    (source_agent_name, target_agent_name, func)
                )


    all_nodes, all_edges = [], []

    # Add a single shared __start__ and __end__ node
    all_nodes.insert(0, {"name": "__start__", "function_name": None, "node_type": "Special", "docstring": None, "source_location": None, "metadata": {}})
    all_nodes.append({"name": "__end__", "function_name": None, "node_type": "Special", "docstring": None, "source_location": None, "metadata": {}})

    for visitor in visitors:
        graph = build_graph_data(visitor)
        all_nodes.extend(graph["nodes"])
        all_edges.extend(graph["edges"])

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"nodes": all_nodes, "edges": all_edges}, f, indent=2)
        print(f"[✓] Swarm agent graph written to {output_file}")
    except Exception as e:
        print(f"Error writing JSON output: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", "-d", type=str, default=".", help="Path to Swarm code")
    parser.add_argument("--output", "-o", type=str, default="swarm_graph.json", help="Path to output JSON")
    args = parser.parse_args()

    extract_swarm_graph(args.directory, args.output)
