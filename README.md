<div align=center><img src="https://github.com/Repello-AI/Agent-Wiz/raw/master/assets/agent_wiz.png" /></div>
<br />

<div align="center">
  <a href="https://pypi.org/project/repello-agent-wiz/">
    <img src="https://img.shields.io/pypi/v/repello-agent-wiz.svg?color=blue" alt="PyPI version">
  </a>
  <a href="https://pypi.org/project/repello-agent-wiz/">
    <img src="https://img.shields.io/pypi/pyversions/repello-agent-wiz.svg" alt="Python Versions">
  </a>
<!--   <a href="https://github.com/Repello-AI/agent-Wiz/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/Repello-AI/agent-Wiz/python-app.yml?label=build" alt="Build Status">
  </a> -->
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/Repello-AI/Agent-Wiz" alt="License">
  </a>
  <a href="code_of_conduct.md">
    <img src="https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg" alt="Contributor Covenant">
  </a>
</div>

<br/>


![](https://github.com/Repello-AI/Agent-Wiz/raw/master/assets/example_vis.png)

## Overview
Agent Wiz is a Python CLI for **extracting agentic workflows** from popular AI frameworks and performing **automated threat assessments** using established threat modeling methodologies. Built for **developers, researchers, and security teams** -  Agent Wiz brings visibility to complex LLM-based orchestration to **visualize flows**, **map tool/agent interactions**, and **generate actionable security reports.** 

## Why Agent Wiz?

In modern LLM-powered systems, agentic workflows are becoming increasingly complex, often involving multiple autonomous agents, tools, and inter-agent communication chains. Agent Wiz helps you bring:

- **Visibility**: Clearly visualize complex agent graphs without manual tracing
- **Structure**: Map relationships between agents, tools, and data flows
- **Security**: Apply threat modeling frameworks to identify potential vulnerabilities

### Core Features

| Capability | Description |
|---------|-------------|
| **Workflow Extraction** | Extract agent-based workflows from code using AST-based static parsing |
| **Threat Vector Visualization** | View agent-to-agent, agent-to-tool, and chained connections in an interactive graph |
| **Automated Threat Assessment** | Generate comprehensive threat assessment report using established threat modeling frameworks for AI agents like MAESTRO|
| **Framework Agnostic** | Works with all major LLM orchestration frameworks |
| **Developer Friendly** | Simple CLI, extensible SDK, and clean JSON exports |



https://github.com/user-attachments/assets/40231eae-9716-421e-a005-fd55d1d8cc71



## Supported Frameworks

The following agent orchestration frameworks are currently supported:

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

- **M**ission: Defining the system purpose and security objectives
- **A**ssets: Inventorying critical components (agents, tools, data flows)
- **E**ntrypoints: Mapping attack surfaces and access vectors
- **S**ecurity Controls: Evaluating existing defensive measures
- **T**hreats: Identifying potential vulnerabilities and attack scenarios
- **R**isks: Calculating impact and likelihood of security events
- **O**perations: Assessing runtime security considerations

Sample threat modelling report generated:

<img src="https://github.com/Repello-AI/Agent-Wiz/raw/master/assets/example_report.png" alt="Threat Modeling Report" />
<br/>


You can also add this line to your `.bashrc`, `.zshrc`, or environment setup script for persistent use.

🧪 More threat models analysis (STRIDE, PASTA, LINDDUN, etc.) are under development.

## Installation

```console
pip install repello-agent-wiz
```

## Prerequisites

Before running any analysis commands, you must set your OpenAI API key as an environment variable:

```console
export OPENAI_API_KEY=sk-...
```

## 🚀 CLI Usage

### 1. Extract Agentic Workflow

```console
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
```console
agent-wiz visualize --input agentchat_graph.json --open
```

This will generate an html d3 based visualisation of the agentic workflow. The `open` flag (optional) and automatically opens the visualization in your default browser.   

### 3. Analyze against Threat Modeling

```console
agent-wiz analyze --input agentchat_graph.json
```

This will generate a report like:  `autogen_report.md`  based on the provided graph and threat modeling frameworks.

__Run agent-wiz --help for more info:__
```console
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

- [Agent Wiz GitHub](https://github.com/Repello-AI/Agent-Wiz)
- [Issue Tracker](https://github.com/Repello-AI/Agent-Wiz/issues)
- [PyPI Package](https://pypi.org/project/repello-agent-wiz/)

<p align="center">
© 2025 Repello AI | <a href="https://repello.ai">Website</a> 
</p>
