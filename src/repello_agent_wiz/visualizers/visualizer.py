import shutil
import json
import importlib.resources as pkg_resources
from pathlib import Path
import http.server
import socketserver
import threading
import time


def generate_visualization(json_path: str, open_browser: bool = False):
    import repello_agent_wiz.visualizers

    # Read framework name from JSON
    with open(json_path, "r") as f:
        graph = json.load(f)
        framework = graph.get("metadata", {}).get("framework", "unknown")

    output_dir = Path(f"{framework}_vis")
    output_dir.mkdir(exist_ok=True)

    # Copy the React build (dist folder contents) from agent_ui
    dist_path = pkg_resources.files(repello_agent_wiz.visualizers).joinpath("agent_ui/dist")
    
    # Copy all files and folders from dist to output_dir
    for item in ["index.html", "assets", "agent.svg"]:
        src = dist_path.joinpath(item)
        dest = output_dir / item
        
        if src.is_file():
            shutil.copy2(src, dest)
        elif src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)

    # Read the index.html and inject the graph data
    index_path = output_dir / "index.html"
    index_text = index_path.read_text(encoding="utf-8")

    # Inject graph data as a global variable before the main script
    json_string = json.dumps(graph, indent=2)
    data_script = f'<script>window.AGENT_GRAPH_DATA = {json_string};</script>'
    
    # Insert the data script before the closing </head> tag
    index_filled = index_text.replace('</head>', f'{data_script}\n  </head>')

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_filled)

    print(f"[✓] Visualization HTML generated at: {output_dir}/index.html")

    if open_browser:
        # Start a simple HTTP server to avoid CORS issues with file:// protocol
        port = 8765
        
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(output_dir.resolve()), **kwargs)
            
            def log_message(self, format, *args):
                pass  # Suppress server logs
        
        def start_server():
            with socketserver.TCPServer(("", port), Handler) as httpd:
                httpd.serve_forever()
        
        # Start server in background thread
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        
        time.sleep(0.5)
        
        import webbrowser
        url = f"http://localhost:{port}/index.html"
        print(f"[✓]Localhost started at {url}")
        print(f"[i] Press Ctrl+C to exit \n")
        webbrowser.open(url)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[✓] Server stopped")

