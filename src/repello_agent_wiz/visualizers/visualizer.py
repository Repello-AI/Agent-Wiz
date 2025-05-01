import shutil
import json
import importlib.resources as pkg_resources
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as PlaywrightError

def generate_visualization(json_path: str, open_browser: bool = False):
    import repello_agent_wiz.templates

    # Read framework name from JSON
    with open(json_path, "r") as f:
        graph = json.load(f)
        framework = graph.get("metadata", {}).get("framework", "unknown")

    output_dir = Path(f"{framework}_vis")
    output_dir.mkdir(exist_ok=True)

    # Copy assets
    assets_path = pkg_resources.files(repello_agent_wiz.templates).joinpath("assets")
    target_assets_path = output_dir / "assets"
    shutil.copytree(assets_path, target_assets_path, dirs_exist_ok=True)

    # Copy index.html and inject graph JSON
    index_template = pkg_resources.files(repello_agent_wiz.templates).joinpath("index.html")
    index_text = index_template.read_text(encoding="utf-8")

    json_string = json.dumps(graph, indent=2)
    index_filled = index_text.replace("const data = {};", f"const data = {json_string};")

    with open(output_dir / "index.html", "w") as f:
        f.write(index_filled)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            html_file_url = f"file://{(output_dir / 'index.html').resolve()}"
            page.goto(html_file_url, wait_until='networkidle') 
            page.screenshot(path=output_dir / "snapshot.png", full_page=True)
            browser.close()
    except PlaywrightError as e:
         print(f"[!] Playwright Error: Could not take snapshot. Is Playwright installed and browsers downloaded (`playwright install`)? Error: {e}")
    except Exception as e:
        print(f"[!] Error during snapshot generation: {e}")

    print(f"[✓] Visualization HTML generated at: {output_dir}/index.html")

    if open_browser:
        import webbrowser
        webbrowser.open(f"file://{(output_dir / 'index.html').resolve()}")

