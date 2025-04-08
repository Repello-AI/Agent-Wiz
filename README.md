# Agent Wiz

[![PyPI version](https://img.shields.io/pypi/v/agent-wiz.svg?color=blue)](https://pypi.org/project/repello-agent-wiz/)
[![License](https://img.shields.io/github/license/Repello-AI/agent-Wiz)](./LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/agent-wiz.svg)](https://pypi.org/project/repello-agent-wiz/)
[![Build](https://img.shields.io/github/actions/workflow/status/Repello-AI/agent-Wiz/python-app.yml?label=build)](https://github.com/Repello-AI/agent-Wiz/actions)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](code_of_conduct.md)

Python CLI for **extracting agentic workflows** from popular AI frameworks and performing **automated security analysis** using threat modeling methodologies.

![](./assets/example_vis.png)

## About Agent Wiz
Agent Wiz is a python CLI for **extracting agentic workflows** from popular AI frameworks and performing **automated security analysis** using threat modeling methodologies. Designed for **developers, researchers, and security teams**, Agent Wiz enables the introspection of LLM-based orchestration logic to **visualize flows**, **map tool/agent interactions**, and **generate security reports** via structured threat modeling frameworks.

## ❓ Why Should You Use It?

In modern LLM-powered systems, agentic workflows are becoming increasingly complex — with dozens of autonomous agents, tools, and inter-agent communication chains. **Agent Wiz** helps you bring visibility, structure, and security to these otherwise opaque systems.

### Key Benefits

- **Understand Complex Agent Graphs**  
  Instantly get clear visibility of agentic workflows in your code, no manual tracing needed! The visualsation clearly specifies various types of connections that can exist in a agentic workflow like Agent-Agent edges, Agent-Tool edges, Tool-Tool chained edges, or even something like Agent Agent edgdes through an intermediate tool. The visualization also highlights the flow of the program by highlighting the start and end nodes.

- **Integrated Security Analysis**  
  Get instant threat modeling reports tailored to your actual orchestration logic. Perfect for audits, red-teaming, or compliance reviews.

- **Developer & Researcher Friendly**  
  Simple CLI, extensible SDK, and clean JSON export — ideal for visualization, automation, or integrating with CI/CD pipelines.

- **Framework Agnostic**  
  Works with all major LLM orchestration stacks like Autogen, LangGraph, CrewAI, LlamaIndex, Swarm, and more.

- **Built for Scale & Insight**  
  Agent Wiz grows with your AI system. Whether you're prototyping or in production — it gives you introspection, fast.

## Supported Frameworks

The following orchestration frameworks are currently supported:

| Framework         | Status  |
|------------------|---------|
| Autogen (core)    | ✅      |
| AgentChat         | ✅      |
| CrewAI            | ✅      |
| LangGraph         | ✅      |
| LlamaIndex        | ✅      |
| n8n               | ✅      |
| OpenAI Agents     | ✅      |
| Pydantic-AI       | ✅      |
| Swarm             | ✅      |

Each framework has its own AST-based static parser to extract:
- Agents (class/function-based)
- Tool functions
- Agent-to-agent transitions
- Tool call chains
- Group agents (e.g., selector, round-robin)


## Security Analysis

Agent Wiz currently supports [**MAESTRO**](https://cloudsecurityalliance.org/blog/2025/02/06/agentic-ai-threat-modeling-framework-maestro) as its primary threat modeling framework. It evaluates agent workflows against the following structure:

- **M**ission
- **A**ssets
- **E**ntrypoints
- **S**ecurity Controls
- **T**hreats
- **R**isks
- **O**perations

Using LLM-backed analysis (GPT-4), a full security report is generated based on your workflow JSON. For example:

<img src="./assets/example_report.png" alt="Threat Modeling Report" />
<br/>

Before running any analysis commands, you **must** set your OpenAI API key as an environment variable:

```bash
export OPENAI_API_KEY=sk-...
```

You can also add this line to your `.bashrc`, `.zshrc`, or environment setup script for persistent use.

🧪 More threat models analysis (STRIDE, PASTA, LINDDUN, etc.) are under development.

## Installation

```bash
pip install repello-agent-wiz
```


## 🚀 CLI Usage

### 1. Extract Agentic Workflow

```bash
agent-wiz extract --framework agent_chat --directory ./examples/code/agent_chat --output agentchat_graph.json
```

This will generate a graph JSON with the following structure:

```json
{
  "nodes": [...],
  "edges": [...],
  "metadata": {
    "framework": "autogen"
  }
}
```

### 2. Visualize the Agentic workflow
```bash
agent-wiz visualize --input agentchat_graph.json --open
```

This will generate an html d3 based visualisation of the agentic workflow. The `open` flag (optional) and automatically opens the visualization in your default browser.

### 3. Analyze against Threat Modeling

```bash
agent-wiz analyze --input agentchat_graph.json
```

This will generate a report like:  `autogen_report.md`  based on the provided graph and threat modeling frameworks.

__Run agent-wiz --help for more info:__
```bash
usage: agent-wiz [-h] {extract,analyze,visualize} ...

Agent Wiz CLI: Extract, Analyze, Visualize agentic workflows.

positional arguments:
  {extract,analyze,visualize}
    extract             Extract graph from source code
    analyze             Run threat modeling analysis on extracted graph
    visualize           Generate HTML visualization from graph JSON

options:
  -h, --help            show this help message and exit
```

## 📈 Roadmap
Planned features (Not in any paricular order)
- [x] Build parsers for major agentic frameworks (Autogen, LangGraph, CrewAI, etc.)
- [x] Generate standardized JSON graph representations of agent flows
- [x] CLI interfaces
- [x] Security report generation
- [ ] Extend to STRIDE, PASTA, LINDDUN, etc.
- [ ] Agent simulation-based threat exploration

## 🤝 Contributing

We welcome contributions of all kinds!

⚠️ Please read [`CONTRIBUTING.md`](./CONTRIBUTING.md) before submitting issues or PRs.


## 📜 Changelog

For recent changes and version history, see [`CHANGELOG.md`](./CHANGELOG.md).

## 📄 License

Licensed under the **Apache 2.0 License**. See [`LICENSE`](./LICENSE) for full details.

## Links

- [Agent Wiz GitHub](https://github.com/Repello-AI/agent-wiz)
- [Issue Tracker](https://github.com/Repello-AI/agent-wiz/issues)
- [PyPI Package](https://pypi.org/project/repello-agent-wiz/)
