import os
import ast
import sys
import json
import argparse
import uuid


class AgentChatMapper(ast.NodeVisitor):
    def __init__(self, filename=None):
        self.current_filename = filename
        self.agents = {}
        self.tools = {}
        self.functions = {}
        self.tool_calls = {}
        self.agent_edges = []
        self.agent_names_to_ids = {}  # Map variable names to agent display names

        self.round_robin_edges = []
        self.selector_memberships = []

    def visit_Assign(self, node):
        if isinstance(node.value, ast.Call) and hasattr(node.value.func, 'id'):
            call_type = node.value.func.id

            # Detect tool assignments
            if call_type == 'FunctionTool':
                if node.targets and isinstance(node.targets[0], ast.Name):
                    tool_var = node.targets[0].id
                    if node.value.args and isinstance(node.value.args[0], ast.Name):
                        func_name = node.value.args[0].id
                        self.tools[tool_var] = [func_name]

            elif node.value.func.id == 'AssistantAgent':
                args = node.value.args
                kwargs = {kw.arg: kw.value for kw in node.value.keywords}

                # Get name from first arg or kwarg
                name_value = kwargs.get("name")
                if not name_value and args and isinstance(args[0], ast.Constant):
                    name_value = args[0]

                # Get tools from kwarg or positional
                tools_value = kwargs.get("tools")
                if not tools_value and len(args) > 2 and isinstance(args[2], ast.List):
                    tools_value = args[2]

                if isinstance(name_value, ast.Constant) and isinstance(name_value.value, str):
                    agent_name = name_value.value
                    agent_var = node.targets[0].id if node.targets and isinstance(node.targets[0], ast.Name) else None

                    self.agents[agent_name] = {
                        "type": "agent",
                        "tools": [],
                        "inherits": ["AssistantAgent"]
                    }

                    if isinstance(tools_value, ast.List):
                        for elt in tools_value.elts:
                            if isinstance(elt, ast.Name):
                                self.agents[agent_name]["tools"].append(elt.id)
                                # ✅ Also register it directly as a tool
                                if elt.id not in self.tools:
                                    self.tools[elt.id] = [elt.id]

                    if agent_var:
                        self.agent_names_to_ids[agent_var] = agent_name


            # RoundRobinGroupChat
            elif node.value.func.id == 'RoundRobinGroupChat':
                args = node.value.args
                if args and isinstance(args[0], ast.List):
                    agent_vars = [elt.id for elt in args[0].elts if isinstance(elt, ast.Name)]
                    agent_ids = [self.agent_names_to_ids.get(var) for var in agent_vars if var in self.agent_names_to_ids]
                    for i in range(len(agent_ids)):
                        src = agent_ids[i]
                        tgt = agent_ids[(i + 1) % len(agent_ids)]  # wrap around
                        self.agent_edges.append((src, tgt, "RoundRobinGroupChat"))

            # SelectorGroupChat
            elif node.value.func.id == 'SelectorGroupChat':
                args = node.value.args
                if args and isinstance(args[0], ast.List):
                    agent_vars = [elt.id for elt in args[0].elts if isinstance(elt, ast.Name)]
                    agent_ids = [self.agent_names_to_ids.get(var) for var in agent_vars if var in self.agent_names_to_ids]

                    group_var = node.targets[0].id if node.targets and isinstance(node.targets[0], ast.Name) else "SelectorGroupChat"

                    for agent_id in agent_ids:
                        self.agent_edges.append((group_var, agent_id, "SelectorGroupChat"))


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
            self.tool_calls[str(current_func)] = list(called_funcs)


def _read_file(file_path):
    try:
        with open(file_path, "r", encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return ""

def build_graph_data(visitor):
    nodes = []
    edges = []

    start = "Start"
    end = "End"

    nodes.append({
        "name": start,
        "function_name": None,
        "docstring": None,
        "node_type": "Start",
        "source_location": None,
        "metadata": {}
    })
    nodes.append({
        "name": end,
        "function_name": None,
        "docstring": None,
        "node_type": "End",
        "source_location": None,
        "metadata": {}
    })

    # Add agents and tools
    for agent_name, agent_data in visitor.agents.items():
        nodes.append({
            "name": agent_name,
            "function_name": "Assistant",
            "docstring": None,
            "node_type": "Agent",
            "source_location": None,
            "metadata": {}
        })

        edges.append({"source": start, "target": agent_name, "condition": {"type": "static"}, "metadata": {}})
        edges.append({"source": agent_name, "target": end, "condition": {"type": "static"}, "metadata": {}})

        for tool_var in agent_data["tools"]:
            tool_funcs = visitor.tools.get(tool_var)
            
            if not tool_funcs:
                print(f"[WARN] Tool variable '{tool_var}' declared in agent '{agent_name}' not found in visitor.tools")
                continue

            for func_name in tool_funcs:
                meta = visitor.functions.get(func_name, {})
                tool_node = {
                    "name": f"{agent_name}_{func_name}",
                    "function_name": func_name,
                    "docstring": meta.get("docstring"),
                    "node_type": "Tool",
                    "source_location": meta.get("source_location"),
                    "metadata": {}
                }
                nodes.append(tool_node)
                edges.append({
                    "source": agent_name,
                    "target": tool_node["name"],
                    "condition": {"type": "static"},
                    "metadata": {}
                })


    # Add tool-to-tool internal calls
    for source_func, called_funcs in visitor.tool_calls.items():
        for target_func in called_funcs:
            for agent_name, agent_data in visitor.agents.items():
                for tool_var in agent_data.get("tools", []):
                    tool_funcs = visitor.tools.get(tool_var, [])
                    if source_func in tool_funcs and target_func in tool_funcs:
                        edges.append({
                            "source": f"{agent_name}_{source_func}",
                            "target": f"{agent_name}_{target_func}",
                            "condition": {"type": "internal_call"},
                            "metadata": {"reason": f"{source_func} calls {target_func}"}
                        })

    # Add agent-to-agent and team-agent edges from group constructs
    for source, target, edge_type in getattr(visitor, "agent_edges", []):
        edge = {
            "source": source,
            "target": target,
            "condition": {
                "type": "group_sequence" if edge_type == "RoundRobinGroupChat" else "member_of_team"
            },
            "metadata": {
                "chat": edge_type
            }
        }

        # Add the group/team node if it's not already in the agent list
        if edge_type == "SelectorGroupChat" and not any(n["name"] == source for n in nodes):
            nodes.append({
                "name": source,
                "function_name": None,
                "docstring": None,
                "node_type": "Team",
                "source_location": None,
                "metadata": {"chat": "SelectorGroupChat"}
            })

        edges.append(edge)


    # Add RoundRobinGroupChat edges
    for source, target in getattr(visitor, "round_robin_edges", []):
        if source and target:
            edges.append({
                "source": source,
                "target": target,
                "condition": {"type": "group_sequence"},
                "metadata": {"chat": "RoundRobinGroupChat"}
            })

    # Add SelectorGroupChat membership edges
    selector_group_names = set(source for source, _ in getattr(visitor, "selector_memberships", []))
    for group in selector_group_names:
        nodes.append({
            "name": group,
            "function_name": None,
            "docstring": None,
            "node_type": "Team",
            "source_location": None,
            "metadata": {"chat": "SelectorGroupChat"}
        })

    for group, member in getattr(visitor, "selector_memberships", []):
        edges.append({
            "source": group,
            "target": member,
            "condition": {"type": "member_of_team"},
            "metadata": {}
        })

    return {
        "nodes": nodes,
        "edges": edges
    }


def merge_visitors(visitors: list) -> AgentChatMapper:
    merged = AgentChatMapper()

    for visitor in visitors:
        # Merge agents
        for agent_name, agent_data in visitor.agents.items():
            if agent_name not in merged.agents:
                merged.agents[agent_name] = agent_data
            else:
                merged.agents[agent_name]["tools"].extend(
                    t for t in agent_data["tools"] if t not in merged.agents[agent_name]["tools"]
                )

        # Merge tools
        for tool_var, func_list in visitor.tools.items():
            if tool_var not in merged.tools:
                merged.tools[tool_var] = list(func_list)
            else:
                merged.tools[tool_var].extend(
                    f for f in func_list if f not in merged.tools[tool_var]
                )

        # Merge function metadata
        merged.functions.update(visitor.functions)

        # Merge tool call graphs
        for source, targets in visitor.tool_calls.items():
            if source not in merged.tool_calls:
                merged.tool_calls[source] = targets
            else:
                merged.tool_calls[source].extend(t for t in targets if t not in merged.tool_calls[source])

        # Merge group sequence edges
        merged.agent_edges.extend([
            edge for edge in visitor.agent_edges if edge not in merged.agent_edges
        ])

        # Merge agent variable mappings
        merged.agent_names_to_ids.update(visitor.agent_names_to_ids)

    return merged

def extract_agentchat_graph(target_directory, output_filename):
    visitors = []
    for root, _, files in os.walk(target_directory):
        for filename in files:
            print(f"Processing file: {filename}")
            if filename.endswith(".py"):
                filepath = os.path.join(root, filename)
                file_content = _read_file(filepath)
                if not file_content:
                    continue
                try:
                    tree = ast.parse(file_content)
                    visitor = AgentChatMapper(filepath)
                    visitor.visit(tree)
                    visitors.append(visitor)

                except SyntaxError as e:
                    print(f"Syntax error in file {filepath}: {e}")
                    continue

    merged_visitor = merge_visitors(visitors)
    graph_data = build_graph_data(merged_visitor)

    if graph_data:
        graph_data["metadata"] = {
            "framework": "AgentChat",
        }

    try:
        with open(output_filename, "w", encoding='utf-8') as outfile:
            json.dump(graph_data, outfile, indent=4)
        print(f"Graph written to {output_filename}")
    except Exception as e:
        print(f"Error writing output: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", "-d", type=str, default=".", help="Target directory")
    parser.add_argument("--output", "-o", type=str, default="agentchat_graph.json", help="Output file")
    args = parser.parse_args()
    extract_agentchat_graph(args.directory, args.output)
