import os
import json
from pathlib import Path

from openai import OpenAI
import importlib.resources as pkg_resources

from repello_agent_wiz import analyzers


def generate_maestro_analysis_report(json_path: str , snapshot_path: str = None):
    # Load embedded files
    with pkg_resources.files(analyzers).joinpath("maestro.txt").open("r") as f:
        maestro = f.read()

    with pkg_resources.files(analyzers).joinpath("sys_prompt.txt").open("r") as f:
        sys_prompt_template = f.read()

    with open(json_path, "r") as f:
        graph_data = json.load(f)
        framework = graph_data.get("metadata", {}).get("framework", "unknown")

    with open(json_path, "r") as f:
        graph_json = f.read()

    sys_prompt = sys_prompt_template.replace("<MAESTRO>", maestro)
    sys_prompt = sys_prompt.replace("<JSON>", graph_json)

    # Initialize the OpenAI client properly
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Use the client instance to create the completion
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": sys_prompt}],
        temperature=0.3
    )

    # Extract content and remove markdown code block if present
    report = response.choices[0].message.content.strip()
    if report.startswith("```") and report.endswith("```"):
        report = "\n".join(report.splitlines()[1:-1]).strip()
    
    output_path = f"{framework}_report.md"

    with open(output_path, "w") as f:
        f.write(report)

    # if snapshot_path is provided, embed the image link in the report 
    if snapshot_path:
        snapshot_path_obj = Path(snapshot_path)
        output_path_obj = Path(output_path)
        if snapshot_path_obj.exists() and snapshot_path_obj.is_file():
            try:
                relative_snapshot_path_str = "" # Initialize variable
                try:
                   snapshot_abs_str = str(snapshot_path_obj.resolve())
                   report_parent_abs_str = str(output_path_obj.resolve().parent)
                   relative_snapshot_path_str = os.path.relpath(snapshot_abs_str, start=report_parent_abs_str)
                except ValueError:
                     print(f"[!] Warning: Cannot create relative path between '{report_parent_abs_str}' and '{snapshot_abs_str}'. Using absolute path.")
                     relative_snapshot_path_str = snapshot_abs_str # Use the resolved absolute string path

                relative_snapshot_path_md = str(Path(relative_snapshot_path_str)).replace("\\", "/")
                image_markdown = f"\n![Workflow Visualization]({relative_snapshot_path_md})\n"

                with open(output_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                insert_pos = -1
                for i, line in enumerate(lines):
                    cleaned_line = line.strip()
                    if cleaned_line.startswith("# "):
                        insert_pos = i + 1
                        break
                    elif cleaned_line:
                        insert_pos = 0 # Fallback: If no H1, insert at the very beginning
                        break
                if insert_pos == -1 and lines:
                    insert_pos = 0
                elif not lines:
                    insert_pos = 0

                if insert_pos != -1:
                    lines.insert(insert_pos, image_markdown)
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    print(f"[✓] Successfully embedded snapshot link into: {output_path}")

            except Exception as e:
                print(f"[!] Warning: Failed to embed snapshot link into report. Error: {e}")
                import traceback
                traceback.print_exc()
        elif snapshot_path:
             print(f"[!] Warning: Snapshot path specified ('{snapshot_path}'), but file does not exist. Cannot embed image link.")
    
    print(f"[✓] Saved MAESTRO analysis to: {output_path}")
