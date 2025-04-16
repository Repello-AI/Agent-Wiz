import argparse
from .frameworks import agent_chat, autogen, crewai, google_adk, langgraph, llama_index, n8n, openai_agents, pydantic, swarm
from .analyzers import generate_maestro_analysis_report
from .visualizers.visualizer import generate_visualization


def main():
    parser = argparse.ArgumentParser(description="Agent Wiz CLI: Extract, Analyze, Visualize agentic workflows.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Extract command ---
    extract_parser = subparsers.add_parser("extract", help="Extract graph from source code")
    extract_parser.add_argument("--framework", "-f", required=True, choices=[
        "agent_chat", "autogen", "crewai", "google_adk" , "langgraph", 
        "llama_index", "n8n", "openai_agents", "pydantic", "swarm"
    ])
    extract_parser.add_argument("--directory", "-d", default=".", help="Directory containing source code")
    extract_parser.add_argument("--output", "-o", default="graph.json", help="Path to output JSON file")

    # --- Analyze command ---
    analyze_parser = subparsers.add_parser("analyze", help="Run threat modeling analysis on extracted graph")
    analyze_parser.add_argument("--input", "-i", required=True, help="Path to JSON graph file")

    # --- Visualize command ---
    visualize_parser = subparsers.add_parser("visualize", help="Generate HTML visualization from graph JSON")
    visualize_parser.add_argument("--input", "-i", required=True, help="Path to JSON graph file")
    visualize_parser.add_argument("--open", action="store_true", help="Automatically open the HTML in a browser")


    # --- Parse and execute ---
    args = parser.parse_args()

    match args.command:
        case "extract":
            match args.framework:
                case "agent_chat":
                    agent_chat.extract_agentchat_graph(args.directory, args.output)
                case "autogen":
                    autogen.extract_autogen_graph(args.directory, args.output)
                case "crewai":
                    crewai.extract_crewai_graph(args.directory, args.output)
                case "google_adk" :
                    google_adk.extract_adk_graph(args.directory, args.output)
                case "langgraph":
                    langgraph.extract_langgraph_graph(args.directory, args.output)
                case "llama_index":
                    llama_index.extract_llamaindex_graph(args.directory, args.output)
                case "pydantic":
                    pydantic.extract_pydantic_ai_graph(args.directory, args.output)
                case "swarm":
                    swarm.extract_swarm_graph(args.directory, args.output)
                case "n8n":
                    n8n.extract_n8n_graph(args.directory, args.output)
                case "openai_agents":
                    openai_agents.extract_openai_agents_graph(args.directory, args.output)
                case _:
                    print(f"Unknown framework: {args.framework}")
    
        case "analyze":
            generate_maestro_analysis_report(args.input)
        
        case "visualize":
            generate_visualization(args.input, open_browser=args.open)
        case _:
            print(f"Unknown command: {args.command}")
            parser.print_help()
            exit(1)
