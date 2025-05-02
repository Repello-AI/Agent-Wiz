import os
import json

from openai import OpenAI
import importlib.resources as pkg_resources

from repello_agent_wiz import analyzers


def generate_maestro_analysis_report(json_path: str):
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
    
    print(f"[✓] Saved MAESTRO analysis to: {output_path}")
