import os
import json
from typing import Dict

from openai import OpenAI
import importlib.resources as pkg_resources

from repello_agent_wiz import analyzers


_METHODOLOGY_CONFIGS: Dict[str, Dict[str, str]] = {
    "maestro": {
        "framework_file": "maestro.txt",
        "prompt_template": "sys_prompt.txt",
        "placeholder": "<MAESTRO>",
        "label": "MAESTRO",
    },
    "stride": {
        "framework_file": "stride.txt",
        "prompt_template": "sys_prompt_stride.txt",
        "placeholder": "<STRIDE>",
        "label": "STRIDE",
    },
}


def generate_analysis_report(json_path: str, methodology: str = "maestro"):
    method_key = methodology.lower()
    if method_key not in _METHODOLOGY_CONFIGS:
        valid = ", ".join(sorted(_METHODOLOGY_CONFIGS))
        raise ValueError(f"Unsupported methodology '{methodology}'. Valid options: {valid}")

    config = _METHODOLOGY_CONFIGS[method_key]

    # Load embedded reference material and prompt template
    with pkg_resources.files(analyzers).joinpath(config["framework_file"]).open("r", encoding="utf-8") as f:
        framework_reference = f.read()

    with pkg_resources.files(analyzers).joinpath(config["prompt_template"]).open("r", encoding="utf-8") as f:
        sys_prompt_template = f.read()

    with open(json_path, "r", encoding="utf-8") as f:
        graph_json = f.read()
        graph_data = json.loads(graph_json)
        framework = graph_data.get("metadata", {}).get("framework", "unknown")

    sys_prompt = sys_prompt_template.replace(config["placeholder"], framework_reference)
    sys_prompt = sys_prompt.replace("<JSON>", graph_json)

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": sys_prompt}],
        temperature=0.3,
    )

    report = response.choices[0].message.content.strip()
    if report.startswith("```") and report.endswith("```"):
        report = "\n".join(report.splitlines()[1:-1]).strip()

    output_path = f"{framework}_{method_key}_report.md"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"[✓] Saved {config['label']} analysis to: {output_path}")


def generate_maestro_analysis_report(json_path: str):
    generate_analysis_report(json_path, methodology="maestro")
