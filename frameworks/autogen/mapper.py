import ast
import json
import inspect
import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union

def get_fqn(node: Union[ast.Name, ast.Attribute]) -> str:
    """Get fully qualified name from AST node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        base = get_fqn(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""

class FunctionAnalyzer(ast.NodeVisitor):
    """
    Analyzes a function body for return values and potential routing targets.
    Includes basic intra-function variable tracking and analysis of simple function calls.
    """
    def __init__(self, outer_visitor, source_node_name: Optional[str] = None, call_site_context: Optional[Dict[str, ast.AST]] = None, depth=0):
        self.outer_visitor = outer_visitor
        self.source_node_name = source_node_name 
        self.call_site_context = call_site_context or {} 
        self.depth = depth
        self.max_depth = 3 # Limit recursion to avoid infinite loops/stack overflows

        self.variables: Dict[str, Set[str]] = {}  
        self.potential_returns: Set[str] = set() 
        self.potential_targets: Set[str] = set()   
        self.has_unresolved_var: bool = False
        self.unresolved_vars: Set[str] = set()

    def _build_call_context(self, func_def: Union[ast.FunctionDef, ast.AsyncFunctionDef], call_node: ast.Call) -> Dict[str, ast.AST]:
        """Maps parameter names of func_def to argument AST nodes from call_node."""
        context = {}
        params = func_def.args.args
        param_names = [p.arg for p in params]

        for i, arg_node in enumerate(call_node.args):
            if i < len(param_names):
                context[param_names[i]] = arg_node

        for kw in call_node.keywords:
            if kw.arg in param_names:
                context[kw.arg] = kw.value

        return context

    def _analyze_called_function(self, func_call_node: ast.Call) -> Set[str]:
        """
        Attempts to analyze the function called and return its potential string return values.
        """
        if self.depth >= self.max_depth:
            print(f"      Max recursion depth ({self.max_depth}) reached, skipping deeper analysis for {self.outer_visitor._stringify_ast_node(func_call_node)}")
            return {f"unresolved_call_max_depth({self.outer_visitor._stringify_ast_node(func_call_node.func)})"}

        resolved_func_name = self.outer_visitor._resolve_fq_name(func_call_node.func)
        func_simple_name = get_fqn(func_call_node.func).split('.')[-1]

        func_def = self.outer_visitor.function_defs.get(func_simple_name) 

        if func_def:
            print(f"      Analyzing call to '{func_simple_name}'...")
            inner_context = self._build_call_context(func_def, func_call_node)

            inner_analyzer = FunctionAnalyzer(self.outer_visitor, call_site_context=inner_context, depth=self.depth + 1)
            try:
                 inner_analyzer.visit(func_def)
                 print(f"      Call to '{func_simple_name}' potentially returns: {inner_analyzer.potential_returns}")
                 return {ret for ret in inner_analyzer.potential_returns if not ret.startswith("variable(") and not ret.startswith("unresolved_")}
            except Exception as e:
                 print(f"      Error analyzing inner function '{func_simple_name}': {e}")
                 return {f"unresolved_call_error({func_simple_name})"}
        else:
            print(f"      Definition for called function '{func_simple_name}' not found.")
            return {f"unresolved_call_not_found({func_simple_name})"}

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_var = node.targets[0].id
            value_node = node.value
            new_values: Set[str] = set() 

            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                new_values.add(value_node.value)
            elif isinstance(value_node, ast.Name):
                if value_node.id in self.variables:
                    new_values.update(self.variables[value_node.id])
                elif value_node.id in self.call_site_context:
                    arg_node = self.call_site_context[value_node.id]
                    resolved_arg = self.outer_visitor.extract_argument_value(arg_node)
                    if isinstance(resolved_arg, str):
                         new_values.add(resolved_arg)
                    else: 
                         new_values.add(f"unresolved_param({value_node.id})")
                else:
                     new_values.add(f"variable({value_node.id})") 

            elif isinstance(value_node, ast.Call):
                 print(f"    Found assignment to '{target_var}' from call: {self.outer_visitor._stringify_ast_node(value_node.func)}")
                 potential_call_returns = self._analyze_called_function(value_node)
                 new_values.update(potential_call_returns)

            if target_var not in self.variables:
                self.variables[target_var] = set()

            if new_values:
                 self.variables[target_var].update(new_values)

        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        if node.value:
            current_potential_returns: Set[str] = set()
            value_node = node.value

            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                current_potential_returns.add(value_node.value)
            elif isinstance(value_node, ast.Name):
                var_name = value_node.id
                if var_name in self.call_site_context:
                    arg_node = self.call_site_context[var_name]
                    resolved_arg = self.outer_visitor.extract_argument_value(arg_node)
                    if isinstance(resolved_arg, str):
                        current_potential_returns.add(resolved_arg)
                    else:
                        current_potential_returns.add(f"unresolved_param_value({var_name})")

                elif var_name in self.variables:
                    current_potential_returns.update(self.variables[var_name])
                else:
                    current_potential_returns.add(f"variable({var_name})")

            self.potential_returns.update(current_potential_returns)

            # Check for routing targets in returns
            if isinstance(value_node, ast.Dict):
                # Look for targets in dictionary returns (typical for routing functions)
                for key, val in zip(value_node.keys, value_node.values):
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        self.potential_targets.add(val.value)

        self.generic_visit(node)

class AutoGenVisitor(ast.NodeVisitor):
    """
    Analyzes Python code to extract AutoGen agent definitions, message handlers, and routing.
    Outputs in a format compatible with LangGraph visualization.
    """
    def __init__(self):
        # Track all nodes (agents and tools)
        self.nodes: List[Dict[str, Any]] = []
        self.node_names: Set[str] = set()
        
        # Track all edges (connections between agents)
        self.edges: List[Dict[str, Any]] = []
        self.edge_signatures: Set[Tuple[str, str, str]] = set()
        
        # Track agent classes and instances
        self.agent_classes: Dict[str, Dict[str, Any]] = {}
        self.agent_instances: Dict[str, Dict[str, Any]] = {}
        
        # Track tool instances
        self.tool_instances: Dict[str, Dict[str, Any]] = {}
        
        # Track variable values and types
        self.variable_values: Dict[str, Any] = {}
        self.variable_types: Dict[str, str] = {}
        
        # Import aliases
        self.import_aliases: Dict[str, str] = {}
        self.import_aliases_fully: Dict[str, str] = {}
        
        # Function and method definitions
        self.function_defs: Dict[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]] = {}
        self.method_handlers: Dict[str, List[Dict[str, Any]]] = {}
        
        # Node function map (similar to LangGraph)
        self.node_function_map: Dict[str, str] = {}
        
        # Graph name
        self.graph_name: Optional[str] = None
        
        # Has special nodes
        self.has_start = False
        self.has_end = False

    def visit_Import(self, node: ast.Import):
        """Process import statements."""
        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            self.import_aliases[local_name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Process from import statements."""
        if node.module is None:
            self.generic_visit(node)
            return

        base_module = node.module
        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            imported_name = alias.name
            self.import_aliases_fully[local_name] = f"{base_module}.{imported_name}"
            
            if '.' in base_module:
                prefix = base_module.split('.')[0]
                if prefix not in self.import_aliases:
                    self.import_aliases[prefix] = prefix
            elif base_module not in self.import_aliases:
                self.import_aliases[base_module] = base_module

        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        """Process class definitions to find agent classes."""
        # Check if this class inherits from any agent base class
        agent_base_classes = [
            "RoutedAgent", 
            "autogen_core.RoutedAgent",
            "Agent", 
            "autogen_core.Agent",
            "DynamicRoutingAgent", 
            "autogen_core.DynamicRoutingAgent"
        ]
        
        is_agent_class = False

        print(node.name, node.bases)
        for base in node.bases:
            base_name = self._resolve_fq_name(base)
            if base_name in agent_base_classes or any(base_name.endswith(cls) for cls in agent_base_classes):
                is_agent_class = True
                break
        
        if is_agent_class:
            self._process_agent_class(node)
            
        self.generic_visit(node)

    def visit_FunctionDef(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        """Process function and method definitions."""
        self.function_defs[node.name] = node
        
        # Check for message_handler decorator
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "message_handler":
                self._process_message_handler(node)
            elif isinstance(decorator, ast.Attribute) and get_fqn(decorator) == "message_handler":
                self._process_message_handler(node)
        
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # Alias for async functions

    def visit_Assign(self, node: ast.Assign):
        """Process assignment statements to track agent and tool instances."""
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            value = node.value

            # Store any value for reference
            self.variable_values[var_name] = value
            
            # Check if this is an agent instantiation
            if isinstance(value, ast.Call):
                call_func = self._resolve_fq_name(value.func)
                
                # Check if instantiating a known agent class
                if call_func in self.agent_classes:
                    self._process_agent_instance(var_name, value, call_func)
                    self.variable_types[var_name] = "agent_instance"
                
                # Check for tool instantiation
                tool_classes = [
                    "Tool", 
                    "FunctionTool", 
                    "autogen_core.tools.Tool",
                    "autogen_core.tools.FunctionTool",
                    "autogen_core.Tool",
                    "autogen_core.FunctionTool"
                ]
                
                if call_func in tool_classes or any(call_func.endswith(cls) for cls in tool_classes):
                    self._process_tool_instance(var_name, value)
                    self.variable_types[var_name] = "tool"
                
                # Check for groups/container creation
                groups_classes = [
                    "GroupChat", 
                    "Group",
                    "autogen.GroupChat", 
                    "autogen.Group"
                ]
                
                if call_func in groups_classes or any(call_func.endswith(cls) for cls in groups_classes):
                    self._process_group_instance(var_name, value)
                    self.variable_types[var_name] = "group"

                # Check if this is initializing a graph
                graph_classes = [
                    "autogen.graph", 
                    "AutoGenGraph"
                ]
                
                if call_func in graph_classes or any(call_func.endswith(cls) for cls in graph_classes):
                    if self.graph_name is None:
                        self.graph_name = var_name
                        print(f"  Identified AutoGen graph instance: {self.graph_name}")

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """Process method calls to identify agent interactions."""
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            instance_name = node.func.value.id
            method_name = node.func.attr
            
            # Check for agent messaging/routing
            if instance_name in self.variable_types:
                if self.variable_types[instance_name] == "agent_instance":
                    if method_name in ["send", "send_message", "initiate_chat", "initiate"]:
                        self._process_agent_messaging(instance_name, node)
                elif self.variable_types[instance_name] == "group":
                    if method_name in ["add_agent", "register"]:
                        self._process_group_registration(instance_name, node)
                    elif method_name in ["initiate_chat", "run", "start"]:
                        self._process_group_start(instance_name, node)

        self.generic_visit(node)

    def _process_agent_class(self, node: ast.ClassDef):
        """Process an agent class definition."""
        class_name = node.name
        agent_id = None
        description = None
        docstring = ast.get_docstring(node)
        
        # Extract potential docstring
        if docstring:
            description = docstring

        print(f"Processing agent class: {class_name}")
        
        for item in node.body:
            print(item.name, "*"*10)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
                for stmt in item.body:
                    if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Attribute):
                        target = stmt.targets[0]
                        if isinstance(target.value, ast.Name) and target.value.id == "self":
                            attr_name = target.attr
                            if attr_name in ["id", "_id", "agent_id", "name"] and isinstance(stmt.value, ast.Constant):
                                agent_id = stmt.value.value
                            elif attr_name in ["description", "_description"] and isinstance(stmt.value, ast.Constant):
                                description = stmt.value.value
                
                for stmt in item.body:
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                        call = stmt.value
                        if isinstance(call.func, ast.Attribute) and call.func.attr == "__init__":
                            if call.args and isinstance(call.args[0], ast.Constant):
                                agent_id = call.args[0].value
        
        # Store the agent class
        self.agent_classes[class_name] = {
            "name": class_name,
            "id": agent_id or class_name,
            "description": description or f"Agent class {class_name}",
            "handlers": []
        }

    def _process_message_handler(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        """Process a message handler method."""
        method_name = node.name
        parent_class = None
        
       
        for class_name, class_info in self.agent_classes.items():
            parent_class = class_name
        
        if parent_class:
            # Extract message type from handler parameters
            message_type = None
            for arg in node.args.args:
                if arg.annotation and isinstance(arg.annotation, ast.Name):
                    message_type = arg.annotation.id
                    break
            
            handler_info = {
                "method": method_name,
                "message_type": message_type,
                "async": isinstance(node, ast.AsyncFunctionDef)
            }
            
            if parent_class not in self.method_handlers:
                self.method_handlers[parent_class] = []
            
            self.method_handlers[parent_class].append(handler_info)
            
            # Update the agent class with this handler
            if parent_class in self.agent_classes:
                self.agent_classes[parent_class]["handlers"].append(handler_info)

    def _process_agent_instance(self, var_name: str, node: ast.Call, class_name: str):
        """Process an agent instance creation."""
        # Extract agent ID if provided in constructor
        agent_id = None
        for i, arg in enumerate(node.args):
            if i == 0 and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                agent_id = arg.value
                break
        
        # Extract from keywords if not found in positional args
        if agent_id is None:
            for kw in node.keywords:
                if kw.arg in ["id", "agent_id", "name"] and isinstance(kw.value, ast.Constant):
                    agent_id = kw.value.value
                    break
        
        # Use class information or fallback to variable name
        if agent_id is None:
            if class_name in self.agent_classes and self.agent_classes[class_name]["id"]:
                agent_id = self.agent_classes[class_name]["id"]
            else:
                agent_id = var_name
        
        # Extract description if provided
        description = None
        for kw in node.keywords:
            if kw.arg in ["description", "system_message"] and isinstance(kw.value, ast.Constant):
                description = kw.value.value
                break
        
        if description is None and class_name in self.agent_classes:
            description = self.agent_classes[class_name].get("description")
        
        # Store the agent instance
        agent_info = {
            "name": var_name,
            "id": agent_id,
            "function_name": class_name,
            "docstring": description,
            "node_type": "Generic",
            "source_location": None,
            "metadata": self._extract_instance_config(node)
        }
        
        self.agent_instances[var_name] = agent_info
        
        # Add the node
        self._add_node_if_not_exists(var_name, agent_info)
        self.node_function_map[var_name] = class_name

    def _process_tool_instance(self, var_name: str, node: ast.Call):
        """Process a tool instance creation."""
        # Extract tool information
        tool_function = None
        description = None
        
        # Extract tool function
        for kw in node.keywords:
            if kw.arg == "func" and isinstance(kw.value, ast.Name):
                tool_function = kw.value.id
            elif kw.arg in ["description", "name"] and isinstance(kw.value, ast.Constant):
                description = kw.value.value
        
        # Create tool info
        tool_info = {
            "name": var_name,
            "function_name": tool_function,
            "docstring": description,
            "node_type": "Tool",
            "source_location": None,
            "metadata": self._extract_instance_config(node)
        }
        
        self.tool_instances[var_name] = tool_info
        
        # Add the node
        self._add_node_if_not_exists(var_name, tool_info)
        if tool_function:
            self.node_function_map[var_name] = tool_function

    def _process_group_instance(self, var_name: str, node: ast.Call):
        """Process a group/groupchat instance creation."""
        # Check for agents list in constructor
        agents_list = []
        
        for kw in node.keywords:
            if kw.arg in ["agents", "members"] and isinstance(kw.value, ast.List):
                # Extract agent variable names from list
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Name):
                        agents_list.append(elt.id)
        
        # Process any initial agents
        for agent_var in agents_list:
            if agent_var in self.agent_instances:
                self._add_connection(
                    var_name,
                    agent_var,
                    "static",
                    {"definition_location": None}
                )

    def _process_group_registration(self, group_var: str, node: ast.Call):
        """Process adding an agent to a group."""
        agent_var = None
        
        # Check args for agent reference
        if node.args and isinstance(node.args[0], ast.Name):
            agent_var = node.args[0].id
        
        if agent_var and agent_var in self.agent_instances:
            # Add connection from group to agent
            self._add_connection(
                group_var,
                agent_var,
                "static", 
                {"definition_location": None}
            )

    def _process_group_start(self, group_var: str, node: ast.Call):
        """Process starting a group conversation."""
        # Find the first agent mentioned
        first_agent = None
        
        # Extract the starting agent if specified
        for kw in node.keywords:
            if kw.arg in ["sender", "initiator", "first_sender"] and isinstance(kw.value, ast.Name):
                first_agent = kw.value.id
                break
        
        # If we found a starting agent, add connections to all other agents in the group
        if first_agent:
            # Find all agents in this group
            for edge in self.edges:
                if edge["source"] == group_var:
                    target_agent = edge["target"]
                    if target_agent != first_agent:
                        self._add_connection(
                            first_agent,
                            target_agent,
                            "conditional_map_list",
                            {
                                "condition_function": "dynamic_routing",
                                "definition_location": None
                            }
                        )

    def _process_agent_messaging(self, source_agent: str, node: ast.Call):
        """Process direct agent messaging."""
        target_agent = None
        
        # Extract target agent from args or keywords
        if node.args and isinstance(node.args[0], ast.Name):
            target_agent = node.args[0].id
        else:
            for kw in node.keywords:
                if kw.arg in ["recipient", "to", "target", "agent", "agent_id"] and isinstance(kw.value, ast.Name):
                    target_agent = kw.value.id
                    break
        
        if target_agent and target_agent in self.agent_instances:
            self._add_connection(
                source_agent,
                target_agent,
                "conditional_map_list",
                {
                    "condition_function": "agent_messaging",
                    "definition_location": None
                }
            )
    
    def _extract_instance_config(self, node: ast.Call) -> Dict[str, Any]:
        """Extract configuration from an instance constructor."""
        config = {}
        
        for kw in node.keywords:
            if isinstance(kw.value, ast.Constant):
                config[kw.arg] = kw.value.value
            elif isinstance(kw.value, ast.List):
                config[kw.arg] = "list_value"
            elif isinstance(kw.value, ast.Dict):
                config[kw.arg] = "dict_value"
            elif isinstance(kw.value, ast.Name):
                config[kw.arg] = f"variable({kw.value.id})"
        
        return config

    def _add_node_if_not_exists(self, name: str, node_info: Dict[str, Any]):
        """Helper to add a node only if its name hasn't been seen."""
        if name == "START": name = "__start__"
        if name == "END": name = "__end__"

        if name not in self.node_names:
            node_data = {
                "name": name,
                "function_name": node_info.get("function_name"),
                "docstring": node_info.get("docstring"),
                "node_type": node_info.get("node_type", "Generic"),
                "source_location": node_info.get("source_location"),
                "metadata": node_info.get("metadata", {})
            }
            
            self.nodes.append(node_data)
            self.node_names.add(name) 
            
            if name == "__start__": self.has_start = True
            if name == "__end__": self.has_end = True

    def _add_connection(self, source: str, target: str, condition_type: str, metadata: Dict[str, Any] = None):
        """Add a connection between nodes if it doesn't already exist."""
        if not metadata:
            metadata = {}
            
        connection_signature = (source, target, condition_type)
        
        if connection_signature not in self.edge_signatures:
            edge_data = {
                "source": source,
                "target": target,
                "condition": {
                    "type": condition_type
                }
            }
            
            # Add condition function if present
            if "condition_function" in metadata:
                edge_data["condition"]["condition_function"] = metadata["condition_function"]
                
            # Add other metadata
            edge_data["metadata"] = {k: v for k, v in metadata.items() if k != "condition_function"}
            
            self.edges.append(edge_data)
            self.edge_signatures.add(connection_signature)

    def _resolve_fq_name(self, node: ast.AST) -> Optional[str]:
        """Resolve fully qualified name from AST node."""
        if isinstance(node, ast.Name):
            node_id = node.id

            if node_id in self.import_aliases_fully:
                return self.import_aliases_fully[node_id]
            return node_id
        elif isinstance(node, ast.Attribute):
            base_node = node.value
            attr_name = node.attr
            base_fqn = self._resolve_fq_name(base_node)
            
            if base_fqn:
                if base_fqn in self.import_aliases:
                    return f"{self.import_aliases[base_fqn]}.{attr_name}"
                else:
                    return f"{base_fqn}.{attr_name}"
            else:
                return None
        return None

    def _stringify_ast_node(self, node: ast.AST) -> str:
        """Convert an AST node to its string representation."""
        if isinstance(node, ast.Constant):
            return repr(node.value)
        elif isinstance(node, ast.Name):
            return node.id
        else:
            try:
                return ast.dump(node)
            except Exception:
                return f"<{type(node).__name__}>"
    
    def extract_argument_value(self, arg: ast.AST) -> Any:
        """Extracts value from simple AST nodes (Constant, Name, List)."""
        if isinstance(arg, ast.Constant):
            value = arg.value
            if value == "START": return "__start__"
            if value == "END": return "__end__"
            return value
        elif isinstance(arg, ast.Name):
            if arg.id == "START": return "__start__"
            if arg.id == "END": return "__end__"
            return arg.id 
        elif isinstance(arg, ast.List):
            return [self.extract_argument_value(elt) for elt in arg.elts]
        elif isinstance(arg, ast.Attribute):
             fqn = get_fqn(arg)
             if fqn.endswith(".START"): return "__start__"
             if fqn.endswith(".END"): return "__end__"
             return fqn 

        print(f"Warning: Cannot extract simple value from argument type {type(arg)}")
        return None
    
    def analyze_functions_for_routing(self):
        print("Analyzing functions for dynamic routing...")
        for node_name, function_name in self.node_function_map.items():
            func_def = self.function_defs.get(function_name)
            if func_def:
                analyzer = FunctionAnalyzer(self, source_node_name=node_name)
                try:
                    analyzer.visit(func_def)
                except Exception as e:
                    print(f"  Error analyzing function '{function_name}' for routing: {e}")
                    continue

                # Process potential routing targets
                for target in analyzer.potential_targets:
                    if target and target in self.node_names:
                        self._add_connection(
                            node_name,
                            target,
                            "conditional_func_return",
                            {
                                "condition_function": function_name,
                                "value": target,
                                "definition_location": None,
                                "potential_return": True
                            }
                        )


def analyze_autogen_file(file_path: str) -> AutoGenVisitor:
    """
    Analyzes a single file and returns the visitor with extracted information.
    """
    visitor = AutoGenVisitor()
    
    try:
        with open(file_path, "r", encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content, filename=file_path)
        print(ast.dump(tree,  indent=4))
        visitor.visit(tree)
        return visitor
    except SyntaxError as e:
        print(f"Warning: Syntax error in {file_path}: {e}")
        return visitor
    except Exception as e:
        print(f"Warning: Error analyzing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return visitor


def merge_visitors(visitors: List[AutoGenVisitor]) -> AutoGenVisitor:
    """
    Merges multiple visitors into a single comprehensive visitor.
    """
    if not visitors:
        return AutoGenVisitor()
    
    merged = visitors[0]
    
    for visitor in visitors[1:]:
        for node in visitor.nodes:
            if node["name"] not in merged.node_names:
                merged.nodes.append(node)
                merged.node_names.add(node["name"])
        
        for edge in visitor.edges:
            edge_sig = (edge["source"], edge["target"], edge["condition"]["type"])
            if edge_sig not in merged.edge_signatures:
                merged.edges.append(edge)
                merged.edge_signatures.add(edge_sig)
        
        for name, func in visitor.function_defs.items():
            if name not in merged.function_defs:
                merged.function_defs[name] = func
        
        for node, func in visitor.node_function_map.items():
            if node not in merged.node_function_map:
                merged.node_function_map[node] = func
        
        for name, info in visitor.agent_instances.items():
            if name not in merged.agent_instances:
                merged.agent_instances[name] = info
        
        for name, info in visitor.tool_instances.items():
            if name not in merged.tool_instances:
                merged.tool_instances[name] = info
        
        for name, info in visitor.agent_classes.items():
            if name not in merged.agent_classes:
                merged.agent_classes[name] = info
    
    return merged


def extract_autogen_structure(directory_path=".") -> Dict[str, Any]:
    """
    Extract AutoGen structure from Python files, in a format compatible with LangGraph.
    """
    print(f"Starting AutoGen structure extraction in directory: {directory_path}")
    
    # List to store visitors from each file
    visitors = []
    
    # Process each Python file
    for root, _, files in os.walk(directory_path):
        for filename in files:
            if filename.endswith(".py"):  # TODO: Add Jupyter notebook support
                filepath = os.path.join(root, filename)
                print(f"Processing file: {filepath}")
                visitor = analyze_autogen_file(filepath)
                visitors.append(visitor)
    
    # Merge all visitors
    merged_visitor = merge_visitors(visitors)
    
    # Run additional analysis to find dynamic routing
    merged_visitor.analyze_functions_for_routing()
    
    # If we found special nodes in the graph, make sure they're added
    if merged_visitor.has_start: 
        merged_visitor._add_node_if_not_exists("__start__", {
            "function_name": None,
            "docstring": None,
            "node_type": "Special",
            "source_location": None,
            "metadata": {}
        })
    
    if merged_visitor.has_end:
        merged_visitor._add_node_if_not_exists("__end__", {
            "function_name": None,
            "docstring": None,
            "node_type": "Special",
            "source_location": None,
            "metadata": {}
        })
    
    # Build graph data structure compatible with LangGraph viz format
    graph_data = {
        "nodes": merged_visitor.nodes,
        "edges": merged_visitor.edges
    }
    
    print(f"Finished AutoGen structure extraction:")
    print(f"  - {len(merged_visitor.nodes)} nodes")
    print(f"  - {len(merged_visitor.edges)} edges")
    
    return graph_data


# --- Main Execution Block ---
if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Extract AutoGen structure from Python files into a LangGraph-compatible format.")
    parser.add_argument("--directory", "-d", type=str, default=".", help="Directory to search for Python files")
    parser.add_argument("--output", "-o", type=str, default="graph_data.json", help="Output JSON file path")
    
    args = parser.parse_args()
    
    target_directory = args.directory
    output_filename = args.output

    # Extract the graph structure
    graph_data = extract_autogen_structure(target_directory)

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
