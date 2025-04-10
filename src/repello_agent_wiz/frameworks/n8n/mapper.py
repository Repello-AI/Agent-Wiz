import json
import os
import sys
import argparse
import re
from pathlib import Path




def simplify_n8n_type(n8n_type_str):
    """Attempts to simplify the full n8n node type string (best effort)."""
    if not n8n_type_str:
        return "Unknown"
    base_type = n8n_type_str.split('/')[-1]
    parts = base_type.split('.')
    if len(parts) > 1:
        base_name = parts[-1]
        simplified = base_name[0].upper() + base_name[1:]
        simplified = re.sub(r'V\d+(\.\d+)*$', '', simplified)
        return simplified
    else:
        return base_type[0].upper() + base_type[1:] if base_type else "Unknown"

def load_categories(filepath="nodes_categorized.json"):
    """Loads category data from a standard JSON object file."""
    category_path = Path(__file__).parent / filepath
    if not category_path.exists():
        print(f"Warning: Category file not found at {category_path}. Categories will not be added.", file=sys.stderr)
        return {}
    try:
        with open(category_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, dict):
                print(f"Error: Category file {category_path} is not a JSON object (key-value pairs) as expected. Found type: {type(data)}", file=sys.stderr)
                return {}
            print(f"Successfully loaded {len(data)} category definitions from {filepath}.")
            return data
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in category file {category_path}: {e}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"Error: Could not read category file {category_path}: {e}", file=sys.stderr)
        return {}



class N8nWorkflowParser:
    """
    Parses n8n workflow JSON into a standardized graph format,
    including synthetic _START_ and _END_ nodes.
    """
    def __init__(self, n8n_data, filename, categories_data):
        self.n8n_data = n8n_data
        self.filename = filename
        self.categories_data = categories_data
        self.nodes = []
        self.edges = []
        self.node_map_by_id = {}
        self.node_map_by_name = {}
        self.parsed_node_ids = set()

    def _determine_node_type(self, n8n_node_name):
        """Determines node type based on node name."""
        if not n8n_node_name:
            return "Generic"
        name_lower = n8n_node_name.lower()
        if "agent" in name_lower:
            return "Agent"
        elif "tool" in name_lower:
            return "Tool"
        else:
            return "Generic"

    def _parse_nodes(self):
        """Extracts and formats node information from n8n data."""
        if 'nodes' not in self.n8n_data or not isinstance(self.n8n_data['nodes'], list):
            print(f"Warning ({self.filename}): 'nodes' array not found or invalid.", file=sys.stderr)
            return

        for node_data in self.n8n_data['nodes']:
            node_id = node_data.get('id')
            if not node_id:
                print(f"Warning ({self.filename}): Skipping node without an 'id': {node_data.get('name', 'Unnamed')}", file=sys.stderr)
                continue

            node_type_raw = node_data.get('type', 'Unknown')

            if node_type_raw == 'n8n-nodes-base.stickyNote':
                continue


            self.parsed_node_ids.add(node_id)


            n8n_node_name = node_data.get('name', '')
            description = n8n_node_name if n8n_node_name else node_id

            output_node_type = self._determine_node_type(n8n_node_name)
            simplified_type_meta = simplify_n8n_type(node_type_raw)

            docstring = node_data.get('notes')
            docstring = docstring if docstring else None

            parameters = node_data.get('parameters', {})

            category_data = self.categories_data.get(node_type_raw, {})
            categories_list = category_data.get('categories', [])
            subcategories_data = category_data.get('subcategories', [])

            primary_category = categories_list[0] if categories_list else None
            primary_subcategory = None
            if primary_category:
                if isinstance(subcategories_data, list):
                    primary_subcategory = subcategories_data[0] if subcategories_data else None
                elif isinstance(subcategories_data, dict):
                    subcats_for_primary = subcategories_data.get(primary_category, [])
                    if isinstance(subcats_for_primary, list) and subcats_for_primary:
                        primary_subcategory = subcats_for_primary[0]

            node_output = {
                "name": node_id,
                "function_name": node_id,
                "docstring": docstring,
                "description": description,
                "node_type": output_node_type,
                "source_location": self.filename,
                "metadata": {
                    "n8n_id": node_id,
                    "n8n_type": node_type_raw,
                    "simplified_n8n_type": simplified_type_meta,
                    "category": primary_category,
                    "subcategory": primary_subcategory,
                    "n8n_categories_list": categories_list,
                    "n8n_subcategories_data": subcategories_data,
                    "parameters": parameters,
                }
            }
            self.nodes.append(node_output)
            self.node_map_by_id[node_id] = node_data

            if n8n_node_name in self.node_map_by_name:
                 print(f"Warning ({self.filename}): Duplicate node name '{n8n_node_name}'. Connection mapping might use the first encountered ID.", file=sys.stderr)

            elif n8n_node_name:
                self.node_map_by_name[n8n_node_name] = node_id


    def _parse_edges(self):
        """Extracts and formats edge information from n8n connections."""
        connections = self.n8n_data.get('connections', {})
        if not isinstance(connections, dict):
             print(f"Warning ({self.filename}): 'connections' is not a dictionary. Found type: {type(connections)}. Skipping edge parsing.", file=sys.stderr)
             return

        for source_node_name, outputs in connections.items():
            source_node_id = self.node_map_by_name.get(source_node_name)

            if not source_node_id:
                if source_node_name in self.node_map_by_id:
                    source_node_id = source_node_name
                else:
                    print(f"Warning ({self.filename}): Could not find source node ID for name '{source_node_name}' in connections. Skipping connections from this node.", file=sys.stderr)
                    continue


            if source_node_id not in self.parsed_node_ids:
                 continue

            if not isinstance(outputs, dict):
                 print(f"Warning ({self.filename}): Expected dictionary for outputs of node '{source_node_name}', found {type(outputs)}. Skipping.", file=sys.stderr)
                 continue

            for output_handle, targets_array in outputs.items():
                if not isinstance(targets_array, list):
                    print(f"Warning ({self.filename}): Expected list for targets from '{source_node_name}' output '{output_handle}', found {type(targets_array)}. Skipping.", file=sys.stderr)
                    continue

                for target_list in targets_array:
                    if not isinstance(target_list, list):
                         print(f"Warning ({self.filename}): Expected inner list for targets from '{source_node_name}' output '{output_handle}', found {type(target_list)}. Skipping.", file=sys.stderr)
                         continue

                    for target_info in target_list:
                        if not isinstance(target_info, dict):
                             print(f"Warning ({self.filename}): Expected dictionary for target info from '{source_node_name}' output '{output_handle}', found {type(target_info)}. Skipping.", file=sys.stderr)
                             continue

                        target_node_name = target_info.get('node')
                        target_input_handle = target_info.get('type', 'main')

                        if not target_node_name:
                            print(f"Warning ({self.filename}): Target connection from '{source_node_name}' (ID: {source_node_id}) via handle '{output_handle}' is missing target node name. Skipping.", file=sys.stderr)
                            continue

                        target_node_id = self.node_map_by_name.get(target_node_name)

                        if not target_node_id:
                            if target_node_name in self.node_map_by_id:
                                target_node_id = target_node_name
                            else:
                                print(f"Warning ({self.filename}): Could not find target node ID for name '{target_node_name}' connected from '{source_node_name}' (ID: {source_node_id}). Skipping this edge.", file=sys.stderr)
                                continue


                        if target_node_id not in self.parsed_node_ids:
                            print(f"Info ({self.filename}): Skipping edge to filtered node '{target_node_name}' (ID: {target_node_id}). It might be a StickyNote.", file=sys.stderr)
                            continue

                        edge_output = {
                            "source": source_node_id,
                            "target": target_node_id,
                            "condition": output_handle,
                            "metadata": {
                                "definition_location": self.filename,
                                "source_handle": output_handle,
                                "target_handle": target_input_handle
                            }
                        }
                        self.edges.append(edge_output)


    def _add_start_end_nodes(self):
        """Adds synthetic _START_ and _END_ nodes and their edges."""
        if not self.parsed_node_ids:
             print(f"Info ({self.filename}): No functional nodes found, skipping _START_/_END_ node addition.")
             return


        start_node = {
            "name": "_START_",
            "function_name": "_START_",
            "docstring": "Synthetic node representing the workflow entry point(s).",
            "description": "Workflow Start",
            "node_type": "WorkflowBoundary",
            "source_location": self.filename,
            "metadata": {
                "n8n_id": "_START_",
                "n8n_type": "synthetic.start",
                "simplified_n8n_type": "StartBoundary",
                "category": "Workflow",
                "subcategory": "Boundary",
                "n8n_categories_list": ["Workflow"],
                "n8n_subcategories_data": ["Boundary"],
                "parameters": {}
            }
        }
        end_node = {
            "name": "_END_",
            "function_name": "_END_",
            "docstring": "Synthetic node representing the workflow terminal point(s).",
            "description": "Workflow End",
            "node_type": "WorkflowBoundary",
            "source_location": self.filename,
            "metadata": {
                "n8n_id": "_END_",
                "n8n_type": "synthetic.end",
                "simplified_n8n_type": "EndBoundary",
                "category": "Workflow",
                "subcategory": "Boundary",
                "n8n_categories_list": ["Workflow"],
                "n8n_subcategories_data": ["Boundary"],
                "parameters": {}
            }
        }


        source_ids_in_edges = {edge['source'] for edge in self.edges}
        target_ids_in_edges = {edge['target'] for edge in self.edges}

        actual_start_node_ids = self.parsed_node_ids - target_ids_in_edges
        actual_end_node_ids = self.parsed_node_ids - source_ids_in_edges


        new_nodes = [start_node] + self.nodes + [end_node]
        new_edges = list(self.edges)


        for start_id in actual_start_node_ids:
            new_edges.append({
                "source": "_START_",
                "target": start_id,
                "condition": "trigger",
                "metadata": {
                    "definition_location": self.filename,
                    "source_handle": "start",
                    "target_handle": "input"
                }
            })


        for end_id in actual_end_node_ids:
             new_edges.append({
                "source": end_id,
                "target": "_END_",
                "condition": "terminal",
                "metadata": {
                    "definition_location": self.filename,
                    "source_handle": "output",
                    "target_handle": "end"
                }
            })


        self.nodes = new_nodes
        self.edges = new_edges


    def parse(self):
        """Parses the entire workflow and returns the structured output."""
        self._parse_nodes()

        if self.parsed_node_ids:
            self._parse_edges()

            self._add_start_end_nodes()
        else:


            pass


        return {"nodes": self.nodes, "edges": self.edges}



def process_file(input_path, output_dir, categories_data):
    """Reads an n8n JSON file, parses it, and writes the output."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_filename = output_dir / f"n8n_graph.json"
    input_filename_str = input_path.name

    print(f"Processing: {input_path}")
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            n8n_data = json.load(f)

        parser = N8nWorkflowParser(n8n_data, input_filename_str, categories_data)
        graph_data = parser.parse()

        if graph_data:
            graph_data["metadata"] = {
                "framework": "n8n",
            }

        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(graph_data, f, indent=2, ensure_ascii=False)

        print(f"Successfully parsed. Output written to: {output_filename}")
        return True

    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in file {input_path}: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: An unexpected error occurred while processing {input_path}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False

def extract_n8n_graph (input_path_str, output_arg_str):

    categories_filepath = "nodes_categorized.json"
    input_path = Path(input_path_str)
    output_arg = Path(output_arg_str)

    if not input_path.exists():
        print(f"Error: Input path does not exist: {input_path}", file=sys.stderr)
        sys.exit(1)

    if input_path.is_dir():
        print(f"Input is a directory: {input_path}")

        if output_arg.suffix.lower() == ".json":
            output_filename = output_arg
        else:
            output_filename = output_arg / "combined_n8n_graph.json"
            output_arg.mkdir(parents=True, exist_ok=True)

        print(f"Output will be combined into: {output_filename}")

        print(f"Loading categories from: {categories_filepath}")
        categories_data = load_categories(categories_filepath)

        input_dir = Path(input_path)
        output_file = Path(output_filename)

        if not input_dir.is_dir():
            print(f"Error: Input path is not a directory: {input_dir}", file=sys.stderr)
            sys.exit(1)


        json_files = sorted(list(input_dir.glob('*.json')))
        if not json_files:
            print(f"Warning: No .json files found in directory: {input_dir}", file=sys.stderr)
            final_graph = {"nodes": [], "edges": []}
            try:
                output_file.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(final_graph, f, indent=2, ensure_ascii=False)
                print(f"Empty combined graph data written to: {output_file}")
                sys.exit(0)
            except Exception as e:
                 print(f"Error: Could not write empty output file {output_file}: {e}", file=sys.stderr)
                 sys.exit(1)


        all_nodes = []
        all_edges = []
        processed_files_count = 0
        failed_files_count = 0

        print(f"\nStarting processing for directory: {input_dir}")
        print(f"Found {len(json_files)} JSON file(s).")

        for file_path in json_files:
            input_filename_str_loop = str(file_path.name)
            print(f"---\nProcessing: {file_path.name}")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    n8n_data = json.load(f)

                parser = N8nWorkflowParser(n8n_data, input_filename_str_loop, categories_data)
                graph_data = parser.parse()

                if graph_data:
                    if graph_data.get("nodes"):
                        all_nodes.extend(graph_data["nodes"])
                        all_edges.extend(graph_data["edges"])
                        print(f"Successfully parsed and added graph data from: {file_path.name}")
                    else:
                        print(f"Info: No functional nodes found in {file_path.name}. Skipping addition to combined graph.")
                    processed_files_count += 1
                else:
                    print(f"Warning: Parsing {file_path.name} returned None or empty data.")
                    failed_files_count += 1

            except json.JSONDecodeError as e:
                print(f"Error: Invalid JSON in file {file_path.name}: {e}", file=sys.stderr)
                failed_files_count += 1
            except Exception as e:
                print(f"Error: An unexpected error occurred while processing {file_path.name}: {e}", file=sys.stderr)
                failed_files_count += 1

        combined_graph = {"nodes": all_nodes, "edges": all_edges}

        print(f"\n---\nFinished processing directory.")
        print(f"Successfully processed: {processed_files_count} file(s)")
        print(f"Failed to process: {failed_files_count} file(s)")
        print(f"Total nodes extracted: {len(all_nodes)}")
        print(f"Total edges extracted: {len(all_edges)}")

        if failed_files_count > 0:
             print(f"Warning: {failed_files_count} file(s) failed to process. The combined graph may be incomplete.", file=sys.stderr)

        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(combined_graph, f, indent=2, ensure_ascii=False)
            print(f"Combined graph data written to: {output_file}")

        except Exception as e:
            print(f"Error: Could not write combined output file {output_file}: {e}", file=sys.stderr)
            sys.exit(1)

        if failed_files_count > 0:
            sys.exit(1)


    elif input_path.is_file():
        print(f"Input is a single file: {input_path}")
        if input_path.suffix.lower() != '.json':
            print(f"Error: Input file is not a .json file: {input_path}", file=sys.stderr)
            sys.exit(1)

        if output_arg.suffix.lower() == ".json":
             print(f"Warning: Output argument '{output_arg}' looks like a file for single file input. Using its parent directory '{output_arg.parent}' for output.", file=sys.stderr)
             output_dir = output_arg.parent
        else:
             output_dir = output_arg

        print(f"Output directory for parsed file: {output_dir}")

        print(f"Loading categories from: {categories_filepath}")
        categories_data = load_categories(categories_filepath)

        if not process_file(input_path, output_dir, categories_data):
             print(f"Failed to process single file: {input_path}", file=sys.stderr)
             sys.exit(1)

    else:
         print(f"Error: Input path is neither a file nor a directory: {input_path}", file=sys.stderr)
         sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Parse n8n workflow JSON files into a standardized graph format.")
    parser.add_argument("input_path", help="Path to a single n8n .json file or a directory containing .json files.")
    parser.add_argument("-o", "--output", default=".", help="Directory to save the parsed output JSON files (default: current directory).")
    args = parser.parse_args()

    extract_n8n_graph(args.input_path, args.output)


if __name__ == "__main__":
    main()