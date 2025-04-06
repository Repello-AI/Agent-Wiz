import argparse
from .frameworks import agent_chat, autogen, crewai, langgraph, llama_index, n8n, openai_agents, pydantic, swarm
from .analyzers import generate_maestro_analysis_report

def main():
    parser = argparse.ArgumentParser(description="Extract agentic graph from Python project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract graph from source code")
    extract_parser.add_argument("--framework", "-f", required=True, choices=[
        "agent_chat", "autogen", "crewai", "langgraph", 
        "llama_index", "n8n", "openai_agents", "pydantic", "swarm"
    ])
    extract_parser.add_argument("--directory", "-d", default=".", help="Directory containing source code")
    extract_parser.add_argument("--output", "-o", default="graph.json", help="Path to output JSON file")

    analyze_parser = subparsers.add_parser("analyze", help="Run MAESTRO analysis on extracted graph")
    analyze_parser.add_argument("--input", "-i", required=True, help="Path to JSON graph file")

    args = parser.parse_args()

    if args.command == "extract":
        match args.framework:
            case "agent_chat":
                agent_chat.extract_agentchat_graph(args.directory, args.output)
            case "autogen":
                autogen.extract_autogen_graph(args.directory, args.output)
            case "crewai":
                crewai.extract_crewai_graph(args.directory, args.output)
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
    elif args.command == "analyze":
        generate_maestro_analysis_report(args.input)
