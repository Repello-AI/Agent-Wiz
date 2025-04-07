import shutil
import json
import importlib.resources as pkg_resources
from pathlib import Path


def generate_visualization(json_path: str, open_browser: bool = False):
    import agent_shadow.templates

    # Read framework name from JSON
    with open(json_path, "r") as f:
        graph = json.load(f)
        framework = graph.get("metadata", {}).get("framework", "unknown")

    output_dir = Path(f"{framework}_vis")
    output_dir.mkdir(exist_ok=True)

    # Copy assets
    assets_path = pkg_resources.files(agent_shadow.templates).joinpath("assets")
    target_assets_path = output_dir / "assets"
    shutil.copytree(assets_path, target_assets_path, dirs_exist_ok=True)

    # Copy index.html and inject graph JSON
    index_template = pkg_resources.files(agent_shadow.templates).joinpath("index.html")
    index_text = index_template.read_text(encoding="utf-8")

    json_string = json.dumps(graph, indent=2)
    index_filled = index_text.replace("const data = {};", f"const data = {json_string};")

    with open(output_dir / "index.html", "w") as f:
        f.write(index_filled)

    print(f"[✓] Visualization HTML generated at: {output_dir}/index.html")

    if open_browser:
        import webbrowser
        webbrowser.open(f"file://{(output_dir / 'index.html').resolve()}")

