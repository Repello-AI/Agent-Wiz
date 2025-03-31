import ast
import json
import inspect
import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union 
def get_fqn(node: Union[ast.Name, ast.Attribute]) -> str:
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        base = get_fqn(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""

import ast
import json
import inspect
import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class FunctionAnalyzer(ast.NodeVisitor):
    """
    Analyzes a function body for return values and Command(goto=...) targets.
    Includes basic intra-function variable tracking and analysis of simple function calls
    assigned to variables used in 'goto'.
    """
    def __init__(self, outer_visitor, source_node_name: Optional[str] = None, call_site_context: Optional[Dict[str, ast.AST]] = None, depth=0):
        self.outer_visitor = outer_visitor
        self.source_node_name = source_node_name 
        self.call_site_context = call_site_context or {} 
        self.depth = depth
        self.max_depth = 3 # Limit recursion to avoid infinite loops/stack overflows

        self.variables: Dict[str, Set[str]] = {}  
        self.potential_returns: Set[str] = set() 
        self.potential_gotos: Set[str] = set()   
        self.has_unresolved_goto_var: bool = False
        self.unresolved_goto_vars: Set[str] = set()

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

            if isinstance(value_node, ast.Call):
                call = value_node
                is_command_call = False
                resolved_func_fqn = self.outer_visitor._resolve_fq_name(call.func)
                if resolved_func_fqn and resolved_func_fqn.endswith('Command'):
                     is_command_call = True

                if is_command_call:
                    for keyword in call.keywords:
                        if keyword.arg == 'goto':
                            goto_value_node = keyword.value
                            resolved_targets = self._resolve_goto_value(goto_value_node)
                            self.potential_gotos.update(resolved_targets)
                            break 

        self.generic_visit(node)

    def _resolve_goto_value(self, value_node: ast.AST) -> Set[str]:
        """Resolves the AST node for a goto argument to potential string targets."""
        targets: Set[str] = set()
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            targets.add(value_node.value)
        elif isinstance(value_node, ast.Name):
            var_name = value_node.id
            resolved = False
            if var_name == "END":
                 targets.add("__end__")
                 resolved = True

            if not resolved and var_name in self.variables:
                resolved_values = self.variables[var_name]
                contains_unresolved = False
                for val in resolved_values:
                    if val.startswith(("variable(", "unresolved_", "function_call(")):
                        contains_unresolved = True
                        self.unresolved_goto_vars.add(val)
                    else:
                        targets.add(val)
                if contains_unresolved:
                     self.has_unresolved_goto_var = True
                resolved = True 

            if not resolved and var_name in self.call_site_context:
                 arg_node = self.call_site_context[var_name]
                 resolved_arg = self.outer_visitor.extract_argument_value(arg_node)
                 if isinstance(resolved_arg, str):
                     targets.add(resolved_arg)
                     resolved = True
                 else:
                      self.has_unresolved_goto_var = True
                      self.unresolved_goto_vars.add(f"unresolved_param_value({var_name})")
                      resolved = True 

            if not resolved:
                self.has_unresolved_goto_var = True
                self.unresolved_goto_vars.add(f"variable({var_name})")

        elif isinstance(value_node, ast.List):
            for element in value_node.elts:
                targets.update(self._resolve_goto_value(element))
        elif isinstance(value_node, ast.Call):
             print(f"    Goto value is a call: {self.outer_visitor._stringify_ast_node(value_node.func)}")
             call_returns = self._analyze_called_function(value_node)
             resolved_targets_from_call = {t for t in call_returns if not t.startswith("unresolved_")}
             unresolved_markers = {t for t in call_returns if t.startswith("unresolved_")}

             if resolved_targets_from_call:
                  targets.update(resolved_targets_from_call)
             if unresolved_markers:
                  self.has_unresolved_goto_var = True
                  self.unresolved_goto_vars.update(unresolved_markers)
             elif not resolved_targets_from_call: 
                  self.has_unresolved_goto_var = True
                  self.unresolved_goto_vars.add(f"unresolved_call_result({self.outer_visitor._stringify_ast_node(value_node.func)})")


        if "END" in targets:
            targets.remove("END")
            targets.add("__end__")

        if "__end__" in targets:
             self.outer_visitor._add_node_if_not_exists("__end__", "Implicit END node", {})

        return targets


class GraphVisitor(ast.NodeVisitor):
    def __init__(self):
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.node_names: Set[str] = set()
        self.function_defs: Dict[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]] = {} 
        self.node_function_map: Dict[str, str] = {} 
        self.dynamic_edge_signatures: Set[Tuple[str, str]] = set()
        self.graph_name: Optional[str] = None 

        # --- State inspired by GraphInstanceTracker ---
        self.graph_class_fqcn = "langgraph.graph.StateGraph"
        self.command_class_fqn = "langgraph.types.Command"   

        self.import_aliases: Dict[str, str] = {} 
        self.import_aliases_fully: Dict[str, str] = {} 

        self.variable_is_target_instance: Dict[str, bool] = {} #

        self.variable_values: Dict[str, Union[ast.List, ast.Dict, ast.Constant]] = {}

        self.has_start = False
        self.has_end = False


    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            self.import_aliases[local_name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
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

    def visit_FunctionDef(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        self.function_defs[node.name] = node
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef # Alias for async functions

    # --- Assignment Handler ---
    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            value = node.value

            if isinstance(value, ast.Call):
                resolved_fqn = self._resolve_fq_name(value.func)
                if resolved_fqn == self.graph_class_fqcn:
                    self.variable_is_target_instance[var_name] = True
                    if self.graph_name is None:
                        self.graph_name = var_name
                        print(f"  Identified StateGraph instance: {self.graph_name}")

            if isinstance(value, (ast.Constant, ast.List, ast.Dict)):
                self.variable_values[var_name] = value
            else:
                if var_name in self.variable_values:
                    del self.variable_values[var_name]

            if isinstance(value, ast.Name) and value.id in self.variable_is_target_instance:
                 self.variable_is_target_instance[var_name] = True


        self.generic_visit(node)

    # --- Call Handler ---
    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            instance_name = node.func.value.id
            if self.variable_is_target_instance.get(instance_name, False):
                method_name = node.func.attr

                processor = getattr(self, f"process_{method_name}", None)
                if processor:
                    try:
                        processor(node)
                    except Exception as e:
                         print(f"Error processing {method_name} call: {e}")

        self.generic_visit(node)

    # --- Helper: Add Node (Ensures Uniqueness) ---
    def _add_node_if_not_exists(self, name, description, metadata):
        """Helper to add a node only if its name hasn't been seen."""
        node_name_to_add = name
        if name == "START": node_name_to_add = "__start__"
        if name == "END": node_name_to_add = "__end__"

        if node_name_to_add not in self.node_names:
            self.nodes.append({"name": node_name_to_add, "description": description, "metadata": metadata})
            self.node_names.add(node_name_to_add) 
            if node_name_to_add == "__start__": self.has_start = True
            if node_name_to_add == "__end__": self.has_end = True

    # --- Helper: Resolve Fully Qualified Name ---
    def _resolve_fq_name(self, node: ast.AST) -> Optional[str]:
        """Attempt to resolve an AST node (Name, Attribute) to a known FQN."""
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

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
             return node.value

        return None 

    # --- Helper: Stringify AST Node (for representation) ---
    def _stringify_ast_node(self, node: ast.AST) -> str:
        """Convert simple AST nodes to string representations."""
        if isinstance(node, ast.Constant):
            return repr(node.value) 
        elif isinstance(node, ast.Name):
            return node.id
        else:
            try:
                 print(f"Cannot Stringify, Dumping AST node: {ast.dump(node)}")
                 return ast.dump(node) # Generic AST dump as fallback
            except Exception:
                 return f"<{type(node).__name__}>"

    # --- Process Methods for Graph Builder Calls ---

    def process_add_node(self, node: ast.Call):
        """Handles add_node, extracts name, maps function, prepares for analysis."""
        node_name = None
        action_ref = None 
        metadata = {}
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}

        if 'node' in kw_args: node_name = self.extract_argument_value(kw_args['node'])
        if 'action' in kw_args: action_ref = self.extract_argument_value(kw_args['action'])

        if len(pos_args) >= 1 and node_name is None:
             arg0_val = self.extract_argument_value(pos_args[0])
             if isinstance(pos_args[0], ast.Constant) and isinstance(arg0_val, str): 
                 node_name = arg0_val
                 if len(pos_args) >= 2 and action_ref is None: 
                     action_ref = self.extract_argument_value(pos_args[1])
             elif isinstance(pos_args[0], ast.Name): 
                 action_ref = arg0_val
                 node_name = action_ref 

        description = action_ref if action_ref else node_name
        if 'metadata' in kw_args and isinstance(kw_args['metadata'], ast.Dict):
            meta_dict = kw_args['metadata']
            try:
                 metadata = {self.extract_argument_value(k): self.extract_argument_value(v)
                             for k, v in zip(meta_dict.keys, meta_dict.values)}
            except Exception: metadata = {"error": "Cannot parse metadata dict"}

        if node_name:
            self._add_node_if_not_exists(node_name, description, metadata)
            if action_ref and isinstance(action_ref, str): # Ensure it's a name string
                self.node_function_map[node_name] = action_ref
        else:
            print(f"Warning: Could not determine node name for add_node call: {self._stringify_ast_node(node)}")


    def process_add_edge(self, node: ast.Call):
        source = None
        target = None
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}

        if 'start_key' in kw_args: source = self.extract_argument_value(kw_args['start_key'])
        if 'end_key' in kw_args: target = self.extract_argument_value(kw_args['end_key'])

        if source is None and len(pos_args) >= 1: source = self.extract_argument_value(pos_args[0])
        if target is None and len(pos_args) >= 2: target = self.extract_argument_value(pos_args[1])

        if source == "__start__": self._add_node_if_not_exists("__start__", "Implicit START node", {})
        if target == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})

        if source and target:
             if isinstance(source, list):
                 for s_item in source:
                     s_item_norm = "__start__" if s_item == "START" else s_item # Normalize if needed in list
                     if s_item_norm == "__start__": self._add_node_if_not_exists("__start__", "Implicit START node", {})
                     if s_item_norm and target: # Ensure items are valid
                        self.edges.append({"source": s_item_norm, "target": target, "conditional": "no", "condition": "", "metadata": {}})
             else: # Single source
                 self.edges.append({"source": source, "target": target, "conditional": "no", "condition": "", "metadata": {}})
        else:
            print(f"Warning: Could not extract source/target from add_edge: {self._stringify_ast_node(node)}")


    def process_add_conditional_edges(self, node: ast.Call):
        source = None
        path_arg = None 
        path_map_arg = None 
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}

        if 'source' in kw_args: source = self.extract_argument_value(kw_args['source'])
        elif len(pos_args) >= 1: source = self.extract_argument_value(pos_args[0])

        if 'path' in kw_args: path_arg = kw_args['path']
        elif len(pos_args) >= 2: path_arg = pos_args[1]

        if 'path_map' in kw_args: path_map_arg = kw_args['path_map']
        elif len(pos_args) >= 3: path_map_arg = pos_args[2]

        if not source:
            print(f"Warning: Missing source in add_conditional_edges: {self._stringify_ast_node(node)}")
            return

        path_func_name = None
        if isinstance(path_arg, ast.Name):
            path_func_name = path_arg.id
        condition_func_info = f" (condition func: {path_func_name})" if path_func_name else ""

        # --- Case 1: path_map is provided ---
        if path_map_arg:
            resolved_path_map = None
            path_map_source_desc = ""

            if isinstance(path_map_arg, ast.Name):
                var_name = path_map_arg.id
                path_map_source_desc = f" (from var: {var_name})"
                if var_name in self.variable_values:
                    resolved_path_map = self.variable_values[var_name] # Should be List or Dict AST node
                else:
                    print(f"Warning: Cannot resolve path_map variable '{var_name}' for source '{source}'.")
                    return
            elif isinstance(path_map_arg, (ast.Dict, ast.List)):
                resolved_path_map = path_map_arg
            else:
                 print(f"Warning: Unsupported path_map type '{type(path_map_arg)}' for source '{source}'.")
                 return

            if isinstance(resolved_path_map, ast.Dict):
                for key_node, value_node in zip(resolved_path_map.keys, resolved_path_map.values):
                    condition_val = self.extract_argument_value(key_node)
                    target_val = self.extract_argument_value(value_node)
                    if target_val == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})
                    if condition_val is not None and target_val:
                        self.edges.append({
                            "source": source, "target": target_val, "conditional": "yes",
                            "condition": f"{condition_val}{condition_func_info}{path_map_source_desc}",
                            "metadata": {}
                        })
            elif isinstance(resolved_path_map, ast.List):
                 # List map: condition is unknown, target is list element
                 for element_node in resolved_path_map.elts:
                     target_val = self.extract_argument_value(element_node)
                     if target_val == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})
                     if target_val:
                          self.edges.append({
                              "source": source, "target": target_val, "conditional": "yes",
                              "condition": f"dynamic_list_map{condition_func_info}{path_map_source_desc}",
                              "metadata": {}
                          })

        # --- Case 2: Only path function is provided (no path_map) ---
        elif path_func_name:
             print(f"  Analyzing path function '{path_func_name}' for conditional edges from '{source}'...")
             func_def = self.function_defs.get(path_func_name)
             if func_def:
                 analyzer = FunctionAnalyzer(self)
                 analyzer.visit(func_def)
                 possible_targets = analyzer.potential_returns - {f"variable({var})" for var in analyzer.variables} # Exclude unresolved variable returns for now

                 if not possible_targets and analyzer.potential_returns:
                      print(f"    Warning: Path function '{path_func_name}' returns unresolved variables: {analyzer.potential_returns}. Cannot determine static targets.")
                      self.edges.append({"source": source, "target": "?", "conditional": "yes", "condition": f"dynamic_unresolved_func{condition_func_info}", "metadata": {}})

                 elif not possible_targets:
                     print(f"    Warning: Could not find any return values in path function '{path_func_name}'.")

                 for target in possible_targets:
                     if target == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})
                     if target: # Ensure target is not empty/None
                        self.edges.append({
                            "source": source, "target": target, "conditional": "yes",
                            "condition": f"dynamic_func_return{condition_func_info}",
                            "metadata": {"potential_return": True}
                        })
             else:
                  print(f"    Warning: Path function '{path_func_name}' definition not found. Cannot determine targets.")
                  self.edges.append({"source": source, "target": "?", "conditional": "yes", "condition": f"dynamic_unknown_func{condition_func_info}", "metadata": {}})

        else:
             print(f"Warning: Insufficient arguments for add_conditional_edges from '{source}'.")


    def process_add_sequence(self, node: ast.Call):
        if len(node.args) == 1 and isinstance(node.args[0], ast.List):
            seq_nodes_ast = node.args[0].elts
            last_node_name = None
            for i, item_ast in enumerate(seq_nodes_ast):
                current_node_name = None
                if isinstance(item_ast, ast.Name): current_node_name = item_ast.id
                elif isinstance(item_ast, ast.Constant) and isinstance(item_ast.value, str): current_node_name = item_ast.value
                elif isinstance(item_ast, ast.Tuple) and len(item_ast.elts) >= 1:
                     name_part = item_ast.elts[0]
                     if isinstance(name_part, ast.Constant) and isinstance(name_part.value, str): current_node_name = name_part.value

                if current_node_name:
                    self._add_node_if_not_exists(current_node_name, current_node_name, {})
                    if last_node_name:
                        self.edges.append({"source": last_node_name, "target": current_node_name, "conditional": "no", "condition": "", "metadata": {}})
                    last_node_name = current_node_name
                else:
                     print(f"Warning: Could not determine node name for item {i} in add_sequence.")
                     last_node_name = None 
        else: print("Warning: add_sequence call did not have a single List argument. Skipping.")


    def process_set_entry_point(self, node: ast.Call):
        target = None
        if len(node.args) == 1: target = self.extract_argument_value(node.args[0])
        for kw in node.keywords:
            if kw.arg == 'key': target = self.extract_argument_value(kw.value)

        if target:
            self._add_node_if_not_exists("__start__", "Implicit START node", {})
            self.edges.append({"source": "__start__", "target": target, "conditional": "no", "condition": "", "metadata": {}})
        else: print("Warning: Could not extract target from set_entry_point call.")


    def process_set_conditional_entry_point(self, node: ast.Call):
        path_arg = None
        path_map_arg = None
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        source = "__start__" 

        self._add_node_if_not_exists("__start__", "Implicit START node", {})

        if 'path' in kw_args: path_arg = kw_args['path']
        elif len(pos_args) >= 1: path_arg = pos_args[0] 

        if 'path_map' in kw_args: path_map_arg = kw_args['path_map']
        elif len(pos_args) >= 2: path_map_arg = pos_args[1] 

        path_func_name = None
        if isinstance(path_arg, ast.Name): path_func_name = path_arg.id
        condition_func_info = f" (condition func: {path_func_name})" if path_func_name else ""

        # --- Case 1: path_map is provided ---
        if path_map_arg:
            resolved_path_map = None
            path_map_source_desc = ""
            if isinstance(path_map_arg, ast.Name):
                var_name = path_map_arg.id
                path_map_source_desc = f" (from var: {var_name})"
                if var_name in self.variable_values: resolved_path_map = self.variable_values[var_name]
                else: print(f"Warning: Cannot resolve path_map variable '{var_name}' for conditional entry point.") ; return
            elif isinstance(path_map_arg, (ast.Dict, ast.List)): resolved_path_map = path_map_arg
            else: print(f"Warning: Unsupported path_map type '{type(path_map_arg)}' for conditional entry point.") ; return

            if isinstance(resolved_path_map, ast.Dict):
                for key_node, value_node in zip(resolved_path_map.keys, resolved_path_map.values):
                    condition_val = self.extract_argument_value(key_node)
                    target_val = self.extract_argument_value(value_node)
                    if target_val == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})
                    if condition_val is not None and target_val:
                        self.edges.append({"source": source, "target": target_val, "conditional": "yes", "condition": f"{condition_val}{condition_func_info}{path_map_source_desc}", "metadata": {}})
            elif isinstance(resolved_path_map, ast.List):
                 for element_node in resolved_path_map.elts:
                     target_val = self.extract_argument_value(element_node)
                     if target_val == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})
                     if target_val: self.edges.append({"source": source, "target": target_val, "conditional": "yes", "condition": f"dynamic_list_map{condition_func_info}{path_map_source_desc}", "metadata": {}})

        # --- Case 2: Only path function is provided ---
        elif path_func_name:
             print(f"  Analyzing path function '{path_func_name}' for conditional entry points...")
             func_def = self.function_defs.get(path_func_name)
             if func_def:
                 analyzer = FunctionAnalyzer(self)
                 analyzer.visit(func_def)
                 possible_targets = analyzer.potential_returns - {f"variable({var})" for var in analyzer.variables}
                 if not possible_targets and analyzer.potential_returns:
                      print(f"    Warning: Path function '{path_func_name}' returns unresolved variables: {analyzer.potential_returns}. Cannot determine static entry targets.")
                      self.edges.append({"source": source, "target": "?", "conditional": "yes", "condition": f"dynamic_unresolved_func{condition_func_info}", "metadata": {}})
                 elif not possible_targets: print(f"    Warning: Could not find any return values in path function '{path_func_name}'.")
                 for target in possible_targets:
                     if target == "__end__": self._add_node_if_not_exists("__end__", "Implicit END node", {})
                     if target: self.edges.append({"source": source, "target": target, "conditional": "yes", "condition": f"dynamic_func_return{condition_func_info}", "metadata": {"potential_return": True}})
             else:
                  print(f"    Warning: Path function '{path_func_name}' definition not found. Cannot determine entry targets.")
                  self.edges.append({"source": source, "target": "?", "conditional": "yes", "condition": f"dynamic_unknown_func{condition_func_info}", "metadata": {}})
        else: print("Warning: Insufficient arguments for set_conditional_entry_point.")


    def process_set_finish_point(self, node: ast.Call):
        source = None
        if len(node.args) == 1: source = self.extract_argument_value(node.args[0])
        for kw in node.keywords:
            if kw.arg == 'key': source = self.extract_argument_value(kw.value)

        if source:
            self._add_node_if_not_exists("__end__", "Implicit END node", {})
            self.edges.append({"source": source, "target": "__end__", "conditional": "no", "condition": "", "metadata": {}})
        else: print("Warning: Could not extract source from set_finish_point call.")


    # --- Argument Value Extractor ---
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


    # --- Goto Analysis (Called AFTER initial visit) ---
    def analyze_functions_for_goto(self):
        """Analyzes mapped functions for Command(goto=...) using FunctionAnalyzer."""
        print("Analyzing functions for dynamic 'goto' edges...")
        for node_name, function_name in self.node_function_map.items():
            func_def = self.function_defs.get(function_name)
            if func_def:
                analyzer = FunctionAnalyzer(self, source_node_name=node_name)
                try:
                    analyzer.visit(func_def)
                except Exception as e:
                    print(f"  Error analyzing function '{function_name}' for gotos: {e}")
                    continue

                for target in analyzer.potential_gotos:
                    edge_signature = (node_name, target)
                    if target and edge_signature not in self.dynamic_edge_signatures:
                         condition_detail = "dynamic (goto)"
                         if analyzer.has_unresolved_goto_var:
                              condition_detail += f" [Unresolved: {', '.join(analyzer.unresolved_goto_vars)}]"

                         self.edges.append({
                             "source": node_name, "target": target, "conditional": "yes",
                             "condition": condition_detail,
                             "metadata": {"dynamic": True, "origin_function": function_name}
                         })
                         self.dynamic_edge_signatures.add(edge_signature)

                if not analyzer.potential_gotos and analyzer.has_unresolved_goto_var:
                     edge_signature = (node_name, "?")
                     if edge_signature not in self.dynamic_edge_signatures:
                          unresolved_vars = ', '.join(analyzer.unresolved_goto_vars)
                          self.edges.append({
                              "source": node_name, "target": "?", "conditional": "yes",
                              "condition": f"dynamic (goto) [Unresolved: {unresolved_vars}]",
                              "metadata": {"dynamic": True, "origin_function": function_name, "unresolved": True}
                          })
                          self.dynamic_edge_signatures.add(edge_signature)

            else:
                if node_name not in ["__start__", "__end__"]:
                    print(f"  Warning: Function '{function_name}' mapped to node '{node_name}' not found in definitions.")


def extract_graph_structure(directory_path=".") -> Dict[str, List[Dict[str, Any]]]:
    """
    Extracts graph structure using the enhanced GraphVisitor.
    """
    visitor = GraphVisitor()
    print(f"Starting graph extraction in directory: {directory_path}")

    for root, _, files in os.walk(directory_path):
        for filename in files:
            if filename.endswith(".py"): # todo -> add ipynb handling
                filepath = os.path.join(root, filename)
                print(f"Processing file: {filepath}")
                try:
                    with open(filepath, "r", encoding='utf-8') as f: content = f.read()
                    tree = ast.parse(content, filename=filepath)
                    visitor.visit(tree)

                except SyntaxError as e: print(f"Warning: Skipping file {filepath} due to SyntaxError: {e}")
                except Exception as e:
                    print(f"Warning: Skipping file {filepath} due to unexpected error: {e}")
                    import traceback
                    traceback.print_exc()

    visitor.analyze_functions_for_goto()

    if visitor.has_start: visitor._add_node_if_not_exists("__start__", "Implicit START node", {})
    if visitor.has_end: visitor._add_node_if_not_exists("__end__", "Implicit END node", {})


    graph_data = {"nodes": visitor.nodes, "edges": visitor.edges}
    print(f"Finished graph extraction. Found {len(visitor.nodes)} nodes and {len(visitor.edges)} edges.")
    return graph_data

# --- Main Execution Block ---
if __name__ == "__main__":
    target_directory = "--name-of-target-directory--" 

    graph_data = extract_graph_structure(target_directory)

    output_filename = "graph_data.json"
    try:
        with open(output_filename, "w", encoding='utf-8') as outfile:
            json.dump(graph_data, outfile, indent=4) # Pretty print
        print(f"Combined graph data written to {output_filename}")
    except Exception as e:
        print(f"Error writing JSON output to {output_filename}: {e}")