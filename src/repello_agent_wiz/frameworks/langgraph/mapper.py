import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

def get_func_name(node: Union[ast.Name, ast.Attribute]) -> str:
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        base = get_func_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""

class FunctionAnalyzer(ast.NodeVisitor):
    def __init__(self, outer_visitor, filename: str, source_node_name: Optional[str] = None, call_site_context: Optional[Dict[str, ast.AST]] = None, depth=0):
        self.outer_visitor = outer_visitor
        self.filename = filename
        self.source_node_name = source_node_name
        self.call_site_context = call_site_context or {}
        self.depth = depth
        self.max_depth = 3

        self.variables: Dict[str, Set[str]] = {}
        self.potential_returns: Set[str] = set()
        self.potential_gotos: Dict[str, Dict[str, Any]] = {}
        self.unresolved_reasons: Set[Tuple[str, str]] = set()
        self.goto_source_locations: Dict[str, Dict[str, Any]] = {}

        self.calls_llm = False
        self.calls_search = False
        self.calls_db = False
        self.calls_tool = False
        self.is_router = False

    def _get_location_dict(self, node: ast.AST) -> Dict[str, Any]:
        return {
            "file": self.filename,
            "line": node.lineno,
            "col": node.col_offset,
            "end_line": getattr(node, 'end_lineno', None),
            "end_col": getattr(node, 'end_col_offset', None),
        }

    def _build_call_context(self, func_def: Union[ast.FunctionDef, ast.AsyncFunctionDef], call_node: ast.Call) -> Dict[str, ast.AST]:
        context = {}
        params = func_def.args.args
        param_names = [p.arg for p in params]
        for i, arg_node in enumerate(call_node.args):
            if i < len(param_names): context[param_names[i]] = arg_node
        for kw in call_node.keywords:
            if kw.arg in param_names: context[kw.arg] = kw.value
        return context

    def _analyze_called_function(self, func_call_node: ast.Call) -> Tuple[Set[str], str, Optional[str]]:
        if self.depth >= self.max_depth:
            func_name_str = self.outer_visitor._stringify_ast_node(func_call_node.func)
            return set(), "unresolved_call_max_depth", func_name_str

        func_simple_name = get_func_name(func_call_node.func).split('.')[-1]
        func_def = self.outer_visitor.function_defs.get(func_simple_name)

        if func_def:
            inner_context = self._build_call_context(func_def, func_call_node)
            inner_analyzer = FunctionAnalyzer(self.outer_visitor, self.filename, call_site_context=inner_context, depth=self.depth + 1)
            try:
                inner_analyzer.visit(func_def)
                resolved_returns = {ret for ret in inner_analyzer.potential_returns if not ret.startswith("variable(") and not ret.startswith("unresolved_")}
                has_unresolved = bool(inner_analyzer.potential_returns - resolved_returns) or bool(inner_analyzer.unresolved_reasons)
                status = "resolved_partial" if resolved_returns and has_unresolved else ("resolved" if resolved_returns else "unresolved_no_returns")
                unresolved_detail = f"inner_call:{func_simple_name}" if has_unresolved else None
                return resolved_returns, status, unresolved_detail
            except Exception as e:
                return set(), "unresolved_call_error", func_simple_name
        else:
            return set(), "unresolved_call_not_found", func_simple_name

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_var = node.targets[0].id
            value_node = node.value
            new_values: Set[str] = set()
            # resolution_status = "unknown"
            # resolution_detail = None

            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                new_values.add(value_node.value)
                # resolution_status = "resolved_literal"
            elif isinstance(value_node, ast.Name):
                source_var = value_node.id
                if source_var in self.variables:
                    new_values.update(self.variables[source_var])
                    resolution_status = "resolved_variable"
                    resolution_detail = source_var
                elif source_var in self.call_site_context:
                    arg_node = self.call_site_context[source_var]
                    resolved_arg = self.outer_visitor.extract_argument_value(arg_node)
                    if isinstance(resolved_arg, str):
                         new_values.add(resolved_arg)
                         resolution_status = "resolved_parameter"
                         resolution_detail = source_var
                    else:
                         resolution_status = "unresolved_parameter_type"
                         resolution_detail = source_var
                         self.unresolved_reasons.add(('parameter', source_var))
                         new_values.add(f"unresolved_param({source_var})")
                else:
                     resolution_status = "unresolved_variable"
                     resolution_detail = source_var
                     self.unresolved_reasons.add(('variable', source_var))
                     new_values.add(f"variable({source_var})")

            elif isinstance(value_node, ast.Call):
                 call_returns, status, detail = self._analyze_called_function(value_node)
                 new_values.update(call_returns)
                 resolution_status = status
                 resolution_detail = detail
                 if status.startswith("unresolved"):
                      self.unresolved_reasons.add(('call_result', detail or self.outer_visitor._stringify_ast_node(value_node.func)))


            if target_var not in self.variables:
                self.variables[target_var] = set()
            if new_values:
                 self.variables[target_var].update(new_values)
            # TODO: Track resolution status per variable? Complex.

        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        if node.value:
            value_node = node.value
            current_potential_returns: Set[str] = set()
            resolution_status = "unknown"
            resolution_detail = None
            goto_target_results : Dict[str, Dict[str, Any]] = {}

            # --- Resolve the value being returned (for direct returns) ---
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                current_potential_returns.add(value_node.value)
                resolution_status = "resolved_literal"
            elif isinstance(value_node, ast.Name):
                var_name = value_node.id
                if var_name in self.call_site_context:
                    arg_node = self.call_site_context[var_name]
                    resolved_arg = self.outer_visitor.extract_argument_value(arg_node)
                    if isinstance(resolved_arg, str):
                        current_potential_returns.add(resolved_arg)
                        resolution_status = "resolved_parameter"
                        resolution_detail = var_name
                    else:
                        resolution_status = "unresolved_parameter_type"
                        resolution_detail = var_name
                        self.unresolved_reasons.add(('parameter', var_name))
                        current_potential_returns.add(f"unresolved_param({var_name})")
                elif var_name in self.variables:
                    current_potential_returns.update(self.variables[var_name])
                    resolution_status = "resolved_variable"
                    resolution_detail = var_name
                    if any(v.startswith("unresolved") or v.startswith("variable") for v in self.variables[var_name]):
                        resolution_status = "resolved_variable_partial"
                        self.unresolved_reasons.add(('variable_propagation', var_name))
                else:
                    resolution_status = "unresolved_variable"
                    resolution_detail = var_name
                    self.unresolved_reasons.add(('variable', var_name))
                    current_potential_returns.add(f"variable({var_name})")

            self.potential_returns.update(r for r in current_potential_returns if not r.startswith('unresolved') and not r.startswith('variable'))
            if len(current_potential_returns) > 1:
                self.is_router = True


            # --- Check for Command(goto=...) ---
            if isinstance(value_node, ast.Call):
                call = value_node
                resolved_func_fqn = self.outer_visitor._resolve_fq_name(call.func)
                is_command_call = resolved_func_fqn and resolved_func_fqn.endswith('Command')

                if resolved_func_fqn:
                    if re.search(r'\b(llm|chat|openai|anthropic|gemini|generate)\b', resolved_func_fqn, re.IGNORECASE):
                        self.calls_llm = True
                    if re.search(r'\b(search|retrieve|tavily|google|bing)\b', resolved_func_fqn, re.IGNORECASE):
                        self.calls_search = True
                    if re.search(r'\b(sql|database|db|query)\b', resolved_func_fqn, re.IGNORECASE):
                        self.calls_db = True
                    if re.search(r'\b(tool|executor)\b', resolved_func_fqn, re.IGNORECASE):
                         self.calls_tool = True

                if is_command_call:
                    for keyword in call.keywords:
                        if keyword.arg == 'goto':
                            goto_value_node = keyword.value
                            goto_target_results = self._resolve_goto_value(goto_value_node)

                            for target, res_info in goto_target_results.items():
                                self.potential_gotos[target] = res_info
                                self.goto_source_locations[target] = self._get_location_dict(node)
                                # if res_info.get("status", "").startswith("unresolved"):
                                    # self.unresolved_reasons.add((res_info.get("type", "goto"), res_info.get("detail", "?")))
                            self.is_router = True
                            break

        self.generic_visit(node)

    def _resolve_goto_value(self, value_node: ast.AST) -> Dict[str, Dict[str, Any]]:
        targets: Dict[str, Dict[str, Any]] = {}


        def add_target(target_str: str, status: str, type_: str, detail: Optional[str]):
            actual_target = "End" if target_str == "END" else target_str
            if actual_target:
                targets[actual_target] = {"status": status, "type": type_, "detail": detail}
                if actual_target == "End":
                     self.outer_visitor._add_node_if_not_exists("End", "Implicit END node", {}, None)

        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            add_target(value_node.value, "resolved_literal", "literal", None)
        elif isinstance(value_node, ast.Name):
            var_name = value_node.id
            if var_name == "END":
                add_target(var_name, "resolved_literal", "literal", "END Constant")
            elif var_name in self.variables:
                possible_values = self.variables[var_name]
                has_unresolved_source = False
                resolved_count = 0
                for val in possible_values:
                    if val.startswith(("variable(", "unresolved_")):
                        has_unresolved_source = True
                        self.unresolved_reasons.add(('variable_propagation', var_name))
                    else:
                        status = "resolved_variable_partial" if has_unresolved_source else "resolved_variable"
                        add_target(val, status, "variable", var_name)
                        resolved_count += 1
                # if resolved_count == 0 and has_unresolved_source:
                    #  targets["?"] = {"status": "unresolved_variable_source", "type": "variable", "detail": var_name}

            elif var_name in self.call_site_context:
                 arg_node = self.call_site_context[var_name]
                 resolved_arg = self.outer_visitor.extract_argument_value(arg_node)
                 if isinstance(resolved_arg, str):
                     add_target(resolved_arg, "resolved_parameter", "parameter", var_name)
                 else:
                    #   add_target("?", "unresolved_parameter_type", "parameter", var_name)
                      self.unresolved_reasons.add(('parameter', var_name))
            else:
                # add_target("?", "unresolved_variable", "variable", var_name)
                self.unresolved_reasons.add(('variable', var_name))

        elif isinstance(value_node, ast.List):
            for i, element in enumerate(value_node.elts):
                element_targets = self._resolve_goto_value(element)
                for target, res_info in element_targets.items():
                    res_info["type"] = "list_element"
                    res_info["detail"] = f"{res_info.get('detail', '')}[{i}]" if res_info.get('detail') else f"index {i}"
                    targets[target] = res_info

        elif isinstance(value_node, ast.Call):
             call_returns, status, detail = self._analyze_called_function(value_node)
             if call_returns:
                 for ret_val in call_returns:
                     add_target(ret_val, status, "call_result", detail or self.outer_visitor._stringify_ast_node(value_node.func))
             elif status.startswith("unresolved"):
                #   add_target("?", status, "call_result", detail or self.outer_visitor._stringify_ast_node(value_node.func))
                  self.unresolved_reasons.add(('call_result', detail or self.outer_visitor._stringify_ast_node(value_node.func)))
             else:
                #  add_target("?", "unresolved_call_no_string_return", "call_result", detail or self.outer_visitor._stringify_ast_node(value_node.func))
                 self.unresolved_reasons.add(('call_result_no_string', detail or self.outer_visitor._stringify_ast_node(value_node.func)))
        else:
             unrec_type = type(value_node).__name__
            #  add_target("?", f"unresolved_goto_value_type", unrec_type, self.outer_visitor._stringify_ast_node(value_node))
             self.unresolved_reasons.add(('goto_value_type', unrec_type))

        return targets


# --- Enhanced GraphVisitor ---

class GraphVisitor(ast.NodeVisitor):
    def __init__(self):
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.node_names: Set[str] = set()
        self.function_defs: Dict[str, Union[ast.FunctionDef, ast.AsyncFunctionDef]] = {}
        self.node_function_map: Dict[str, str] = {}
        self.graph_name: Optional[str] = None
        self.current_filename: str = ""

        self.graph_class_fqcn = "langgraph.graph.StateGraph"
        self.command_class_fqn = "langgraph.types.Command"

        self.import_aliases: Dict[str, str] = {}
        self.import_aliases_fully: Dict[str, str] = {}
        self.variable_is_target_instance: Dict[str, bool] = {}
        self.variable_values: Dict[str, Union[ast.List, ast.Dict, ast.Constant]] = {}

        self.has_start = False
        self.has_end = False

    def _get_location_dict(self, node: ast.AST) -> Dict[str, Any]:
        if not self.current_filename or not hasattr(node, 'lineno'):
             return None
        return {
            "file": self.current_filename,
            "line": node.lineno,
            "col": node.col_offset,
            "end_line": getattr(node, 'end_lineno', None),
            "end_col": getattr(node, 'end_col_offset', None),
        }

    def visit_file(self, tree: ast.AST, filename: str):
        self.current_filename = filename
        self.visit(tree)
        self.current_filename = ""

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
                 if prefix not in self.import_aliases: self.import_aliases[prefix] = prefix
            elif base_module not in self.import_aliases: self.import_aliases[base_module] = base_module
        self.generic_visit(node)

    def visit_FunctionDef(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        self.function_defs[node.name] = node
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

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
            if isinstance(value, (ast.Constant, ast.List, ast.Dict)):
                self.variable_values[var_name] = value
            else:
                if var_name in self.variable_values: del self.variable_values[var_name]
            if isinstance(value, ast.Name) and value.id in self.variable_is_target_instance:
                 self.variable_is_target_instance[var_name] = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            instance_name = node.func.value.id
            if self.variable_is_target_instance.get(instance_name, False):
                method_name = node.func.attr
                processor = getattr(self, f"process_{method_name}", None)
                if processor:
                    try: processor(node)
                    except Exception as e: print(f"Error processing {method_name} call at {self._get_location_dict(node)}: {e}")
        self.generic_visit(node)

    def _add_node_if_not_exists(self, name, function_name, metadata, location_dict):
        node_name_to_add = "Start" if name == "START" else "End" if name == "END" else name
        is_implicit = name in ["START", "END" , "__start__" , "__end__"]
        if node_name_to_add not in self.node_names:
            node_data = {
                "name": node_name_to_add,
                "function_name": function_name if not is_implicit else None,
                "docstring": None,
                "node_type": "Special" if is_implicit else "Unknown",
                "source_location": location_dict if not is_implicit else None,
                "metadata": metadata or {}
            }

            if not is_implicit and function_name:
                 func_def = self.function_defs.get(function_name)
                 print(func_def)
                 if func_def:
                     try:
                          docstring = ast.get_docstring(func_def)
                          print(docstring)
                          node_data["docstring"] = docstring
                          node_data["node_type"] = self._classify_node(node_name_to_add, function_name, func_def)
                     except Exception as e:
                          print(f"Warning: Could not process function '{function_name}' for node '{node_name_to_add}': {e}")
                 else :
                     try :
                          node_data["node_type"] = self._classify_node(node_name_to_add, function_name, None)
                     except Exception as e:
                          print(f"Warning: Could not process function '{function_name}' for node '{node_name_to_add}': {e}")


            self.nodes.append(node_data)
            self.node_names.add(node_name_to_add)
            if node_name_to_add == "Start": self.has_start = True
            if node_name_to_add == "End": self.has_end = True


    def _classify_node(self, node_name: str, function_name: Optional[str], func_def: Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]]) -> str:
        if not function_name and not node_name: return "Unknown"

        check_name = node_name.lower() if node_name else ""
        check_func = function_name.lower() if function_name else ""
        combined_check = f"{check_name} {check_func}".strip()

        if check_name in ["start", "start_node", "__start__", "begin", "begin_node"]:
            return "Start"
        if check_name in ["input", "input_node", "entry", "entry_point"]:
            return "Input"
        if check_name in ["output", "output_node", "__end__", "end", "finish", "exit", "finish_point"]:
            return "Output"
        if check_name in ["tool_node", "tools", "tool_executor", "actions", "action_node"]:
            return "ToolExecutor"

        if re.search(r'\b(router|route|routing|conditional|condition|branch|switch|decide|decision|choose|selector|map_step|path_map)\b', combined_check):
            return "Router"

        if re.search(r'\b(agent|llm|openai|anthropic|gemini|llama|mistral|claude|chat|generate|invoke|call_model|prompt|predict|reasoning|step_back)\b', combined_check):
            return "Agent"

        if re.search(r'\b(plan|planner|planning|sequence|orchestrate|orchestrator)\b', combined_check):
            return "Planner"

        if re.search(r'\b(search|retrieve|retriever|tavily|google|bing|duckduckgo|arxiv|wikipedia|lookup|knowledge)\b', combined_check):
            return "Search/Retriever"

        is_tool_related = (
            re.search(r'\b(tool(?!_node)|action(?!_node)|api|function_call|calculator|python_repl|terminal)\b', combined_check) or
            "tool" in check_name.split("_") or
            "tools" in check_name.split("_") or
            re.search(r'[_-]tools?\b', check_name)
        )

        if is_tool_related and not re.search(r'\b(search|retrieve|database|sql)\b', combined_check):
             if func_def:
                analyzer = FunctionAnalyzer(self, getattr(func_def, '_filename', self.current_filename), source_node_name=node_name)
                try:
                    analyzer.visit(func_def)
                    if analyzer.calls_tool: return "Tool"
                except Exception: pass
             return "Tool"


        if re.search(r'\b(db|database|sql|query|vectorstore|storage|memory|persist)\b', combined_check):
             if re.search(r'\b(memory|context|history)\b', combined_check) and not re.search(r'\b(sql|query|vectorstore)\b', combined_check):
                 return "MemoryManagement"
             else:
                 return "Database/Storage"

        if re.search(r'\b(reflect|reflection|review|critique|critic|correct|refine|revise|validate|check)\b', combined_check):
            return "Reflection/Correction"

        if re.search(r'\b(input|entry|receive|user_input|question)\b', combined_check):
            return "Input"
        if re.search(r'\b(output|exit|finish|display|respond|response|answer|result)\b', combined_check):
            return "Output"


        if func_def:
            func_filename = getattr(func_def, '_filename', self.current_filename)
            analyzer = FunctionAnalyzer(self, func_filename, source_node_name=node_name)
            try:
                analyzer.visit(func_def)
                if analyzer.is_router: return "Router"
                if analyzer.calls_llm: return "Agent/LLM"
                if analyzer.calls_search: return "Search/Retriever"
                if analyzer.calls_db: return "Database/Storage"
                if analyzer.calls_tool: return "Tool"
            except Exception as e:
                 pass

        print(node_name)
        return "Generic"

    def _resolve_fq_name(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            node_id = node.id
            if node_id in self.import_aliases_fully: return self.import_aliases_fully[node_id]
            return node_id
        elif isinstance(node, ast.Attribute):
            base_node = node.value
            attr_name = node.attr
            base_fqn = self._resolve_fq_name(base_node)
            if base_fqn:
                if base_fqn in self.import_aliases: return f"{self.import_aliases[base_fqn]}.{attr_name}"
                else: return f"{base_fqn}.{attr_name}"
            else: return None
        elif isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
        return None

    def _stringify_ast_node(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant): return repr(node.value)
        elif isinstance(node, ast.Name): return node.id
        else:
            try: return ast.dump(node)
            except Exception: return f"<{type(node).__name__}>"

    # --- Process Methods (Enhanced) ---

    def process_add_node(self, node: ast.Call):
        node_name = None
        action_ref = None
        metadata = {}
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        location = self._get_location_dict(node)


        if 'node' in kw_args: node_name = self.extract_argument_value(kw_args['node'])
        if 'action' in kw_args: action_ref = self.extract_argument_value(kw_args['action'])
        if len(pos_args) >= 1 and node_name is None:
             arg0_val = self.extract_argument_value(pos_args[0])
             if isinstance(pos_args[0], ast.Constant) and isinstance(arg0_val, str):
                 node_name = arg0_val
                 if len(pos_args) >= 2 and action_ref is None: action_ref = self.extract_argument_value(pos_args[1])
             elif isinstance(pos_args[0], ast.Name) or isinstance(pos_args[0], ast.Attribute):
                 action_ref = self.extract_argument_value(pos_args[0])
                 node_name = action_ref

        if 'metadata' in kw_args and isinstance(kw_args['metadata'], ast.Dict):
            try: metadata = {self.extract_argument_value(k): self.extract_argument_value(v) for k, v in zip(kw_args['metadata'].keys, kw_args['metadata'].values)}
            except Exception: metadata = {"error": "Cannot parse metadata dict"}

        if node_name and isinstance(node_name, str):
            actual_func_name = action_ref if isinstance(action_ref, str) else node_name
            print(action_ref)
            self._add_node_if_not_exists(node_name, actual_func_name, metadata, location)
            if actual_func_name:
                self.node_function_map[node_name] = actual_func_name
        else:
            print(f"Warning: Could not determine node name for add_node call at {location}")

    def process_add_edge(self, node: ast.Call):
        source = None
        target = None
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        location = self._get_location_dict(node)

        if 'start_key' in kw_args: source = self.extract_argument_value(kw_args['start_key'])
        if 'end_key' in kw_args: target = self.extract_argument_value(kw_args['end_key'])
        if source is None and len(pos_args) >= 1: source = self.extract_argument_value(pos_args[0])
        if target is None and len(pos_args) >= 2: target = self.extract_argument_value(pos_args[1])

        if source == "Start": self._add_node_if_not_exists("START", None, {}, None)
        if target == "End": self._add_node_if_not_exists("END", None, {}, None)

        if source and target:
             sources = source if isinstance(source, list) else [source]
             for s_item in sources:
                 s_item_norm = "Start" if s_item == "START" else s_item
                 if s_item_norm == "Start": self._add_node_if_not_exists("START", None, {}, None)
                 if s_item_norm and target:
                     self.edges.append({
                         "source": s_item_norm,
                         "target": target,
                         "condition": {"type": "static"},
                         "metadata": {"definition_location": location}
                     })
        else:
            print(f"Warning: Could not extract source/target from add_edge at {location}")


    def process_add_conditional_edges(self, node: ast.Call):
        source = None
        path_arg = None
        path_map_arg = None
        pos_args = node.args
        kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        location = self._get_location_dict(node)

        if 'source' in kw_args: source = self.extract_argument_value(kw_args['source'])
        elif len(pos_args) >= 1: source = self.extract_argument_value(pos_args[0])

        if 'path' in kw_args: path_arg = kw_args['path']
        elif len(pos_args) >= 2: path_arg = pos_args[1]

        if 'path_map' in kw_args: path_map_arg = kw_args['path_map']
        elif len(pos_args) >= 3: path_map_arg = pos_args[2]

        if not source:
            print(f"Warning: Missing source in add_conditional_edges at {location}")
            return

        path_func_name = None
        if isinstance(path_arg, ast.Name): path_func_name = path_arg.id
        elif isinstance(path_arg, ast.Attribute): path_func_name = get_func_name(path_arg)

        if path_map_arg:
            resolved_path_map = None
            map_source_detail = ""
            resolution_status = "unknown"

            if isinstance(path_map_arg, ast.Name):
                var_name = path_map_arg.id
                map_source_detail = f"variable: {var_name}"
                if var_name in self.variable_values and isinstance(self.variable_values[var_name], (ast.Dict, ast.List)):
                    resolved_path_map = self.variable_values[var_name]
                    resolution_status = "resolved_variable"
                else:
                    print(f"Warning: Cannot resolve path_map variable '{var_name}' for source '{source}' at {location}.")
                    resolution_status = "unresolved_variable"
                    # self.edges.append({
                    #     "source": source, "target": "?",
                    #     "condition": {
                    #         "type": "conditional_map",
                    #         "value": None,
                    #         "condition_function": path_func_name,
                    #         # "resolution": {"status": resolution_status, "detail": map_source_detail}
                    #     },
                    #     "metadata": {"definition_location": location}
                    # })
                    return
            elif isinstance(path_map_arg, (ast.Dict, ast.List)):
                resolved_path_map = path_map_arg
                resolution_status = "resolved_literal"
                map_source_detail = "literal"
            else:
                 print(f"Warning: Unsupported path_map type '{type(path_map_arg)}' for source '{source}' at {location}.")
                 resolution_status = "unresolved_type"
                 map_source_detail = type(path_map_arg).__name__
                #  self.edges.append({ "source": source, "target": "?", "condition": { "type": "conditional_map", "value": None, "condition_function": path_func_name, "resolution": {"status": resolution_status, "detail": map_source_detail}}, "metadata": {"definition_location": location}})
                 return

            if isinstance(resolved_path_map, ast.Dict):
                for key_node, value_node in zip(resolved_path_map.keys, resolved_path_map.values):
                    condition_val = self.extract_argument_value(key_node)
                    target_val = self.extract_argument_value(value_node)
                    if target_val == "End": self._add_node_if_not_exists("END", None, {}, None)
                    if condition_val is not None and target_val:
                        self.edges.append({
                            "source": source, "target": target_val,
                            "condition": {
                                "type": "conditional_map",
                                "value": condition_val,
                                "condition_function": path_func_name,
                                # "resolution": {"status": resolution_status, "detail": map_source_detail}
                            },
                            "metadata": {"definition_location": location}
                        })
            elif isinstance(resolved_path_map, ast.List):
                 for element_node in resolved_path_map.elts:
                     target_val = self.extract_argument_value(element_node)
                     if target_val == "End": self._add_node_if_not_exists("END", None, {}, None)
                     if target_val:
                          self.edges.append({
                              "source": source, "target": target_val,
                              "condition": {
                                  "type": "conditional_map_list",
                                  "value": None,
                                  "condition_function": path_func_name,
                                #   "resolution": {"status": resolution_status, "detail": map_source_detail}
                              },
                              "metadata": {"definition_location": location}
                          })

        elif path_func_name:
             func_def = self.function_defs.get(path_func_name)
             if func_def:
                 analyzer = FunctionAnalyzer(self, self.current_filename)
                 analyzer.visit(func_def)
                 possible_targets = analyzer.potential_returns

                 if not possible_targets and analyzer.unresolved_reasons:
                      detail = ", ".join([f"{t}:{d}" for t, d in analyzer.unresolved_reasons])
                      print("Warning : no target")
                    #   self.edges.append({
                    #         "source": source, "target": "?",
                    #         "condition": {
                    #             "type": "conditional_func_return",
                    #             "value": None,
                    #             "condition_function": path_func_name,
                    #             # "resolution": {"status": "unresolved_function_analysis", "detail": detail}
                    #         },
                    #         "metadata": {"definition_location": location}
                    #   })
                 elif not possible_targets:
                     print(f"Warning: Path function '{path_func_name}' seems to have no direct string return values at {location}.")
                    #  self.edges.append({
                    #        "source": source, "target": "?",
                    #        "condition": { "type": "conditional_func_return", "value": None, "condition_function": path_func_name, "resolution": {"status": "unresolved_no_returns"}},
                    #        "metadata": {"definition_location": location}
                    #  })

                 for target in possible_targets:
                     if target == "End": self._add_node_if_not_exists("END", None, {}, None)
                     if target:
                        self.edges.append({
                            "source": source, "target": target,
                            "condition": {
                                "type": "conditional_func_return",
                                "value": target,
                                "condition_function": path_func_name,
                                # "resolution": {"status": "resolved"}
                            },
                            "metadata": {"definition_location": location, "potential_return": True}
                        })
             else:
                  print(f"Warning: Path function '{path_func_name}' definition not found at {location}.")
                #   self.edges.append(
                #       {"source": source,
                #        "target": "?",
                #        "condition": {"type": "conditional_func_return", "value": None,
                #                       "condition_function": path_func_name,
                #                     # "resolution": {"status": "unresolved_function_not_found"}
                #                     },
                #                     "metadata": {"definition_location": location}})
        else:
             print(f"Warning: Insufficient arguments for add_conditional_edges from '{source}' at {location}.")


    def process_add_sequence(self, node: ast.Call):
        location = self._get_location_dict(node)
        if len(node.args) == 1 and isinstance(node.args[0], ast.List):
            seq_nodes_ast = node.args[0].elts
            last_node_name = None
            for i, item_ast in enumerate(seq_nodes_ast):
                current_node_name = self.extract_argument_value(item_ast)
                if isinstance(current_node_name, list) and len(current_node_name) > 0 and isinstance(current_node_name[0], str):
                    current_node_name = current_node_name[0]

                if isinstance(current_node_name, str):
                    self._add_node_if_not_exists(current_node_name, current_node_name, {}, self._get_location_dict(item_ast))
                    if last_node_name:
                        self.edges.append({
                            "source": last_node_name,
                            "target": current_node_name,
                            "condition": {"type": "sequence"},
                            "metadata": {"definition_location": location}
                        })
                    last_node_name = current_node_name
                else:
                     print(f"Warning: Could not determine node name for item {i} in add_sequence at {location}.")
                     last_node_name = None
        else: print(f"Warning: add_sequence call malformed at {location}.")


    def process_set_entry_point(self, node: ast.Call):
        target = None
        location = self._get_location_dict(node)
        if len(node.args) == 1: target = self.extract_argument_value(node.args[0])
        for kw in node.keywords:
            if kw.arg == 'key': target = self.extract_argument_value(kw.value)

        if target:
            self._add_node_if_not_exists("START", None, {}, None)
            self.edges.append({
                "source": "Start",
                "target": target,
                "condition": {"type": "entry_point"},
                "metadata": {"definition_location": location}
            })
        else: print(f"Warning: Could not extract target from set_entry_point at {location}.")

    def process_set_conditional_entry_point(self, node: ast.Call):
         path_arg = None
         path_map_arg = None
         pos_args = node.args
         kw_args = {kw.arg: kw.value for kw in node.keywords if kw.arg}
         location = self._get_location_dict(node)
         source = "Start"

         self._add_node_if_not_exists("START", None, {}, None)

         if 'path' in kw_args: path_arg = kw_args['path']
         elif len(pos_args) >= 1: path_arg = pos_args[0]

         if 'path_map' in kw_args: path_map_arg = kw_args['path_map']
         elif len(pos_args) >= 2: path_map_arg = pos_args[1]

         path_func_name = None
         if isinstance(path_arg, ast.Name): path_func_name = path_arg.id
         elif isinstance(path_arg, ast.Attribute): path_func_name = get_func_name(path_arg)

         if path_map_arg:
            resolved_path_map = None
            map_source_detail = ""
            # resolution_status = "unknown"
            if isinstance(path_map_arg, ast.Name):
                 var_name = path_map_arg.id
                 map_source_detail = f"variable: {var_name}"
                 if var_name in self.variable_values and isinstance(self.variable_values[var_name], (ast.Dict, ast.List)):
                      resolved_path_map = self.variable_values[var_name]; resolution_status = "resolved_variable"
                #  else: resolution_status = "unresolved_variable"
            elif isinstance(path_map_arg, (ast.Dict, ast.List)):
                 resolved_path_map = path_map_arg; resolution_status = "resolved_literal"; map_source_detail = "literal"
            else:
                # resolution_status = "unresolved_type";
                map_source_detail = type(path_map_arg).__name__

            # if resolution_status.startswith("unresolved"):
            #      self.edges.append({"source": source, "target": "?", "condition": {"type": "conditional_entry_map", "value": None, "condition_function": path_func_name, "resolution": {"status": resolution_status, "detail": map_source_detail}}, "metadata": {"definition_location": location}})
            #      return

            if isinstance(resolved_path_map, ast.Dict):
                 for key_node, value_node in zip(resolved_path_map.keys, resolved_path_map.values):
                      condition_val = self.extract_argument_value(key_node)
                      target_val = self.extract_argument_value(value_node)
                      if target_val == "End": self._add_node_if_not_exists("END", None, {}, None)
                      if condition_val is not None and target_val: self.edges.append({"source": source, "target": target_val, "condition": {"type": "conditional_entry_map", "value": condition_val, "condition_function": path_func_name, "resolution": {"status": resolution_status, "detail": map_source_detail}}, "metadata": {"definition_location": location}})
            elif isinstance(resolved_path_map, ast.List):
                  for element_node in resolved_path_map.elts:
                       target_val = self.extract_argument_value(element_node)
                       if target_val == "End": self._add_node_if_not_exists("END", None, {}, None)
                       if target_val: self.edges.append({"source": source, "target": target_val, "condition": {"type": "conditional_entry_map_list", "value": None, "condition_function": path_func_name, "resolution": {"status": resolution_status, "detail": map_source_detail}}, "metadata": {"definition_location": location}})

         elif path_func_name:
            func_def = self.function_defs.get(path_func_name)
            if func_def:
                 analyzer = FunctionAnalyzer(self, self.current_filename); analyzer.visit(func_def)
                 possible_targets = analyzer.potential_returns
                 if not possible_targets and analyzer.unresolved_reasons:
                      print("Warning :  no target")
                      detail = ", ".join([f"{t}:{d}" for t, d in analyzer.unresolved_reasons])
                    #   self.edges.append({"source": source, "target": "?", "condition": {"type": "conditional_entry_func", "value": None, "condition_function": path_func_name, "resolution": {"status": "unresolved_function_analysis", "detail": detail}}, "metadata": {"definition_location": location}})
                #  elif not possible_targets: self.edges.append({"source": source, "target": "?", "condition": {"type": "conditional_entry_func", "value": None, "condition_function": path_func_name, "resolution": {"status": "unresolved_no_returns"}}, "metadata": {"definition_location": location}})
                 for target in possible_targets:
                      if target == "End": self._add_node_if_not_exists("END", None, {}, None)
                      if target: self.edges.append({"source": source, "target": target, "condition": {"type": "conditional_entry_func", "value": target, "condition_function": path_func_name, "resolution": {"status": "resolved"}}, "metadata": {"definition_location": location, "potential_return": True}})
            # else: self.edges.append({"source": source, "target": "?", "condition": {"type": "conditional_entry_func", "value": None, "condition_function": path_func_name, "resolution": {"status": "unresolved_function_not_found"}}, "metadata": {"definition_location": location}})
         else: print(f"Warning: Insufficient arguments for set_conditional_entry_point at {location}.")


    def process_set_finish_point(self, node: ast.Call):
        source = None
        location = self._get_location_dict(node)
        if len(node.args) == 1: source = self.extract_argument_value(node.args[0])
        for kw in node.keywords:
            if kw.arg == 'key': source = self.extract_argument_value(kw.value)

        if source:
            self._add_node_if_not_exists("END", None, {}, None)
            self.edges.append({
                "source": source,
                "target": "End",
                "condition": {"type": "finish_point"},
                "metadata": {"definition_location": location}
            })
        else: print(f"Warning: Could not extract source from set_finish_point at {location}.")


    def extract_argument_value(self, arg: ast.AST) -> Any:
        if isinstance(arg, ast.Constant):
            value = arg.value
            if value == "START": return "Start"
            if value == "END": return "End"
            return value
        elif isinstance(arg, ast.Name):
            if arg.id == "START": return "Start"
            if arg.id == "END": return "End"
            if arg.id in self.variable_values:
                 return self.extract_argument_value(self.variable_values[arg.id])
            return arg.id
        elif isinstance(arg, ast.List):
            return [self.extract_argument_value(elt) for elt in arg.elts]
        elif isinstance(arg, ast.Dict):
             try:
                 return {self.extract_argument_value(k): self.extract_argument_value(v) for k, v in zip(arg.keys, arg.values)}
             except Exception:
                 return f"<Dict: {self._stringify_ast_node(arg)}>"
        elif isinstance(arg, ast.Attribute):
             fqn = get_func_name(arg)
             if fqn.endswith(".START"): return "Start"
             if fqn.endswith(".END"): return "End"
             return fqn
        elif isinstance(arg, ast.Call):
            fqn = get_func_name(arg.func)
            return fqn
        print( f"<Unsupported: {self._stringify_ast_node(arg)}>")
        return "_"


    def analyze_functions_for_goto(self):
        print("Analyzing functions for dynamic 'goto' edges...")
        analyzed_signatures = set()

        for node_name, function_name in self.node_function_map.items():
            func_def = self.function_defs.get(function_name)
            if func_def:
                func_filename = self.current_filename
                for fn, tree_node in self.function_defs.items():
                    if fn == function_name and hasattr(tree_node, '_filename'):
                        func_filename = tree_node._filename
                        break

                analyzer = FunctionAnalyzer(self, func_filename, source_node_name=node_name)
                try: analyzer.visit(func_def)
                except Exception as e: print(f"  Error analyzing function '{function_name}' for gotos: {e}"); continue

                for target, resolution_info in analyzer.potential_gotos.items():
                    edge_signature = (node_name, target)
                    if target and edge_signature not in analyzed_signatures:
                        origin_location = analyzer.goto_source_locations.get(target)
                        self.edges.append({
                             "source": node_name,
                             "target": target,
                             "condition": {
                                 "type": "exit_point" if target == "End" else "entry_point" if target == "Start" else "dynamic_goto"
                                #  "value": None,
                                #  "resolution": resolution_info
                             },
                             "metadata": {
                                 "origin_function": function_name,
                                 "origin_location": origin_location
                             }
                         })
                        analyzed_signatures.add(edge_signature)

                # if not analyzer.potential_gotos and analyzer.unresolved_reasons:
                #     edge_signature = (node_name, "?")
                #     if edge_signature not in analyzed_signatures:
                #          details = ", ".join(sorted([f"{t}:{d}" for t, d in analyzer.unresolved_reasons]))
                #          origin_location = None
                #          self.edges.append({
                #              "source": node_name, "target": "?",
                #              "condition": {
                #                 "type": "unknown"
                #                 #  "value": None,
                #                 #  "resolution": {"status": "unresolved", "detail": details}
                #              },
                #              "metadata": {"origin_function": function_name, "origin_location": origin_location, "unresolved": True}
                #          })
                #          analyzed_signatures.add(edge_signature)
            else:
                if node_name not in ["Start", "End"]:
                    print(f"  Warning: Function '{function_name}' mapped to node '{node_name}' not found in definitions.")


def extract_langgraph_graph(directory_path: str, output_filename: str):
    visitor = GraphVisitor()
    print(f"Starting graph extraction in directory: {directory_path}")

    if not os.path.isdir(directory_path):
         print(f"Error: Provided path '{directory_path}' is not a valid directory.", file=sys.stderr)
         sys.exit(1)

    all_files_processed = []
    parse_errors = 0

    for root, dirs, files in os.walk(directory_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', 'env', '__pycache__', 'node_modules', '.git']]

        for filename in files:
            if filename.endswith(".py"):
                filepath = os.path.join(root, filename)
                all_files_processed.append(filepath)
                print(f"Processing file: {filepath}")
                try:
                    with open(filepath, "r", encoding='utf-8') as f: content = f.read()
                    tree = ast.parse(content, filename=filepath)

                    for node in ast.walk(tree):
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                             node._filename = filepath 

                    visitor.visit_file(tree, filepath) 

                except SyntaxError as e:
                    print(f"Warning: Skipping file {filepath} due to SyntaxError: {e}", file=sys.stderr)
                    parse_errors += 1
                except Exception as e:
                    print(f"Warning: Skipping file {filepath} due to unexpected error: {e}", file=sys.stderr)
                    parse_errors += 1

    try:
        visitor.analyze_functions_for_goto()
    except Exception as e:
        print(f"Error during post-processing analysis: {e}", file=sys.stderr)
        sys.exit(1)


    graph_data = {"nodes": visitor.nodes, "edges": visitor.edges}

    if graph_data:
        graph_data["metadata"] = {
            "framework": "Langgraph",
        }
    
    print(f"Finished graph extraction. Found {len(visitor.nodes)} nodes and {len(visitor.edges)} edges across {len(all_files_processed)} files ({parse_errors} file processing errors).")


    output_path = Path(output_filename)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        graph_data["nodes"].sort(key=lambda x: x.get("id", ""))
        graph_data["edges"].sort(key=lambda x: (x.get("source", ""), x.get("target", "")))

        with open(output_path, "w", encoding='utf-8') as outfile:
            json.dump(graph_data, outfile, indent=2) 
        print(f"Combined graph data written to {output_path}")

    except Exception as e:
        print(f"Error writing JSON output to {output_path}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract LangGraph structures from Python code into a graph JSON.")
    parser.add_argument("directory", type=str, nargs='?', default=".", help="Directory containing the Python code (default: current directory).")
    parser.add_argument("-o", "--output", type=str, default="langgraph_graph_output.json", help="Output JSON filename (default: langgraph_graph_output.json).")
    args = parser.parse_args()

    target_directory = args.directory
    output_filename = args.output

    extract_langgraph_graph(target_directory, output_filename)