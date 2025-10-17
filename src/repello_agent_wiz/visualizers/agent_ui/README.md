# Reactflow-based Agentic Workflow Visualization Migration Guide

## Summary of Changes

This guide documents the migration from d3-based visualization to ReactFlow visualization for Agent-Wiz.

## What Was Changed

### 1. **graphLoader.tsx** - Dynamic JSON Loading
**File:** `src/repello_agent_wiz/visualizers/agent_ui/src/graphLoader.tsx`

**Changes:**
- Added support for runtime JSON injection via `window.AGENT_GRAPH_DATA`
- Maintains backward compatibility with static JSON import as fallback
- The app now checks for runtime data first, then falls back to the bundled JSON

### 2. **visualizer.py** - React Build Integration
**File:** `src/repello_agent_wiz/visualizers/visualizer.py`

**Changes:**
- Now copies React build files from `agent_ui/dist/` instead of d3 templates
- Injects graph JSON data as `window.AGENT_GRAPH_DATA` into the HTML
- Maintains the same CLI interface and behavior

**Key improvements:**
- Uses the same `{framework}_vis` output directory structure
- Injects data before the React app loads
- Opens in browser with `--open` flag just like before

### 3. **pyproject.toml** - Package Configuration
**File:** `pyproject.toml`

**Changes:**
- Added React build files to package data:
```toml
"repello_agent_wiz.visualizers" = ["agent_ui/dist/*", "agent_ui/dist/assets/*"]
```

This ensures the React build is included when the package is distributed.

---

## How It Works

### Current Flow (After Migration)

1. **User runs CLI command:**
   ```bash
   agent-wiz visualize --input agentchat_graph.json --open
   ```

2. **Python backend (`visualizer.py`):**
   - Reads the graph JSON file
   - Creates output directory: `{framework}_vis/`
   - Copies React build files from `agent_ui/dist/`
   - Injects graph data into HTML as `window.AGENT_GRAPH_DATA`
   - Opens the HTML file in browser

3. **React frontend (`agent_ui`):**
   - Loads in browser
   - Checks for `window.AGENT_GRAPH_DATA` (injected by Python)
   - Renders the graph using ReactFlow
   - Shows interactive visualization with search, details panel, etc.

---

## Next Steps to Complete Integration

### Step 1: Build the React App
You need to build the React app to generate the production-ready files in the `dist` folder:

```bash
cd src/repello_agent_wiz/visualizers/agent_ui
npm install 
npm run build
```

This will:
- Compile TypeScript to JavaScript
- Bundle all assets and dependencies
- Generate optimized production files in `dist/`
- Include the modified `graphLoader.tsx` with dynamic JSON support

### Step 2: Test the Integration
After building, test that everything works:

```bash
# Navigate back to project root by running the .venv and activating the python bin
cd Agent-Wiz/
python -m venv .venv 
source .venv/bin/activate

# Test with an existing graph JSON
agent-wiz visualize --input agentchat_graph.json --open
```

This should:
- ✅ Create a directory called `agent_chat_vis/` (or based on framework name in JSON)
- ✅ Copy the React build files into it
- ✅ Inject the graph data
- ✅ Open the React-based visualization in the browser

### Step 3: Verify the Visualization
When the browser opens, you should see:
- Interactive ReactFlow graph with the agent nodes
- Search functionality to find specific nodes
- Details panel showing node information when clicked
- MiniMap for navigation
- Theme toggle (dark/light mode)
- All the features from our React app

### Step 4: Test with Different Frameworks
Test with other framework graphs to ensure compatibility:

```bash
# Example with other graphs
agent-wiz visualize --input agentchat_graph.json --open   
agent-wiz visualize --input examples/output/crewai_graph.json --open
agent-wiz visualize --input examples/output/langgraph_graph.json --open
```

---

## Troubleshooting

### Issue: CORS Policy Blocking (Critical - Primary Issue Encountered)

**Root Cause:**
Modern browsers (Chrome, Firefox, Safari, Edge) **block ES modules loaded via the `file://` protocol** for security reasons, even when using relative paths. This is a **security feature**, not a bug. The issue occurs because:

1. **Line 8-9 in `dist/index.html`:**
   ```html
   <script type="module" crossorigin src="./assets/index-C-4ri22Q.js"></script>
   <link rel="stylesheet" crossorigin href="./assets/index-CFHXm2Y_.css">
   ```
   The `type="module"` attribute triggers ES6 module loading, which requires CORS headers.

2. **Browser Security Model:**
   - When opening files with `file://` protocol, the origin is `null`
   - Browsers don't send CORS headers for local file system requests
   - ES modules require CORS headers to load, creating a deadlock

3. **Why Relative Paths Don't Help:**
   Even with `base: './'` in `vite.config.ts`, the paths are relative but still accessed via `file://` protocol, which lacks the HTTP infrastructure needed for module loading.

**Technical Details:**
- **Affected Files:** All JavaScript bundles with `type="module"` attribute
- **Error Code:** `net::ERR_FAILED` 
- **Origin:** `null` (file:// protocol has no origin)
- **Protocol Schemes Allowed:** `http`, `https`, `chrome-extension`, `data`, `isolated-app`
- **Vite Build System:** Generates ES modules by default, which require HTTP protocol

**Resolution Implemented:**

Modified `visualizer.py` (lines 53-87) to start an HTTP server when `--open` flag is used:

```python
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
    
    # Give server time to start
    time.sleep(0.5)
    
    # Open browser
    import webbrowser
    url = f"http://localhost:{port}/index.html"
    print(f"[✓] localhost started at {url}")
    print(f"[i] Press Ctrl+C to exit \n")
    webbrowser.open(url)
    
    # Keep the server running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[✓] Server stopped")
```

**Key Changes:**
1. **HTTP Server:** Uses Python's built-in `http.server.SimpleHTTPRequestHandler`

**Result:**
- ✅ Files served via `http://localhost:8765` instead of `file://`
- ✅ CORS headers automatically provided by HTTP server
- ✅ ES modules load without security restrictions
- ✅ All assets (JS, CSS, SVG) load correctly
- ✅ No changes needed to React code or build configuration

**Alternative Solutions (Not Implemented):**

1. **Browser Flags (Not Recommended):**
   ```bash
   # Chrome with disabled security (INSECURE - DO NOT USE IN PRODUCTION)
   chrome --disable-web-security --user-data-dir=/tmp/chrome
   ```
   ❌ Security risk, requires user to modify browser settings

2. **Build to Non-Module Script (Not Ideal):**
   ```js
   // vite.config.ts
   build: { target: 'es2015', rollupOptions: { output: { format: 'iife' } } }
   ```
   ❌ Loses module benefits, larger bundle size, compatibility issues

3. **Inline All Assets (Impractical):**
   ❌ Huge HTML files, poor performance, maintenance nightmare

**Best Practice:** 
Always serve modern web applications via HTTP/HTTPS. The implemented solution is industry-standard and aligns with how Vite, Create React App, and other modern tools handle local development and preview.

---

### Issue: "React build not found"
**Solution:** Make sure you've run `npm run build` in the `agent_ui` directory.

### Issue: "Graph not loading / showing empty"
**Solution:** 
1. Check the browser console for errors
2. Verify the JSON structure matches expected format
3. Ensure `window.AGENT_GRAPH_DATA` is injected (view page source)

### Issue: "Assets not loading (icons, styles)"
**Solution:**
1. Check that all files from `dist/` were copied
2. Verify the `assets/` folder is included in the output directory
3. Check the vite config for base path settings

### Issue: "Package installation fails"
**Solution:**
If distributing as a Python package, ensure:
1. React build is up to date
2. `pyproject.toml` includes all necessary files
3. Run `pip install -e .` to reinstall in development mode

---

## Development Workflow

### For Python Changes
1. Modify `visualizer.py`
2. Test with: `agent-wiz visualize --input test.json --open`

### For React Changes
1. Modify files in `agent_ui/src/`
2. Test locally: `cd agent_ui && npm run dev`
3. Build for production: `npm run build`
4. Test integration: `agent-wiz visualize --input test.json --open`

---

## Dagre Layout Algorithm Integration

### Overview

The visualization system uses **Dagre** (Directed Acyclic Graph Rendering Engine) to automatically compute optimal node positions in the graph. This ensures clean, organized layouts without manual positioning.

**File:** `src/graphLoader.tsx` (lines 47-70)

### What is Dagre?

Dagre is a JavaScript library that implements a **hierarchical graph layout algorithm**. It:
- Positions nodes to minimize edge crossings
- Arranges nodes in ranks (layers) based on their relationships
- Computes edge routing to avoid overlaps
- Supports customizable spacing and orientation

**Key Concepts:**
- **Rank:** Vertical or horizontal layer where nodes are placed
- **Node Separation:** Minimum horizontal spacing between nodes in same rank
- **Rank Separation:** Minimum vertical spacing between ranks
- **Direction:** Graph orientation (LR = Left-to-Right, TB = Top-to-Bottom, etc.)

---

## File Structure After Migration

```
Agent-Wiz/
├── src/repello_agent_wiz/
│   ├── visualizers/
│   │   ├── agent_ui/              # React app source
│   │   │   ├── src/               # React components
│   │   │   │   ├── App.tsx
│   │   │   │   ├── graphLoader.tsx  # Modified for dynamic loading
│   │   │   │   └── ...
│   │   │   ├── dist/              # Build output (used by Python)
│   │   │   │   ├── index.html
│   │   │   │   ├── assets/
│   │   │   │   └── ...
│   │   │   ├── package.json
│   │   │   └── vite.config.ts
│   │   └── visualizer.py          # Modified to use React build
│   └── cli.py                     # Unchanged (calls visualizer.py)
├── pyproject.toml                 # Modified to include React build
```

---

## Output Directory Structure

When running `agent-wiz visualize --input agentchat_graph.json --open`, it creates:

```
agent_chat_vis/
├── index.html           # React app entry point with injected data
├── assets/              # Bundled JS, CSS, and other assets
│   ├── index-*.js       # React app bundle
│   ├── index-*.css      # Styles
│   └── ...
└── agent.svg            # App icon
```

This directory is standalone and can be:
- Opened directly in any browser
- Hosted on a http server
- Shared with others
- Archived for later reference

---

## Benefits of This Approach

✅ **Modern UI:** ReactFlow provides a much better interactive experience than d3
✅ **Maintainability:** React components are easier to maintain and extend
✅ **Backward Compatible:** Same CLI interface, no breaking changes for users
✅ **Dynamic Loading:** Can visualize any graph JSON without rebuilding
✅ **Professional Features:** Search, zoom, pan, details panel, theming, etc.
✅ **Fast Development:** Vite provides hot reload during development
