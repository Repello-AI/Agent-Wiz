# 🤝 Contributing to Agent Wiz

Thank you for considering contributing to Agent Wiz! 🎉  
We welcome contributions of all types – bug reports, feature requests, documentation, testing, and code.

---

## 📦 Getting Started

1. **Fork** the repository.
2. **Clone** your forked repo:
   ```bash
   git clone https://github.com/your-username/agent-wiz.git
   cd agent-wiz
   ```
3. **Create a virtual environment**:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   ```
4. **Install in editable mode with dev dependencies**:
   ```bash
   pip install -e .
   ```
5. **Test your changes locally:**
   run using `agent-wiz` command as instructed in [cli_usage](https://github.com/Repello-AI/Agent-Wiz?tab=readme-ov-file#-cli-usage])

---

## ⚙️ Troubleshooting

Make sure you are in the Agent-Wiz root directory (where pyproject.toml is)
Make sure your virtual environment (venv) is active

1. Uninstall completely

```bash
pip uninstall repello-agent-wiz -y
```

2. Optional: Clean build artifacts (just in case)

```bash
rm -rf build dist src/repello_agent_wiz.egg-info
```

3. Reinstall in editable mode, disabling cache

```bash
pip install --no-cache-dir -e .
```

4. Verify again

```bash
pip list | grep repello-agent-wiz
```

This should point to a path inside your virtual environment's `bin` directory, like:
`/path/to/your/Agent-Wiz/venv/bin/agent-wiz`

---

## 🛠️ Guidelines

### 📄 Code Style

- Use `black` for formatting.
- Follow [PEP8](https://peps.python.org/pep-0008/).

### 📝 Commits

- Use clear, descriptive commit messages.
- Prefix with context: `feat:`, `fix:`, `refactor:`, `docs:`, etc.

---

## 🔄 Pull Requests

1. Ensure your branch is up-to-date with `main`.
2. Submit your PR with a clear description:
   - What was added/changed
   - Why it’s needed
   - Any related issue or discussion

> Link to issues using GitHub keywords: `closes #X`, `resolves #Y`

---

## 💡 Feature Suggestions

Have an idea or enhancement?  
Open an issue with a detailed explanation and example.

---

## 🤝 Code of Conduct

We expect all contributors to follow our [Code of Conduct](./CODE_OF_CONDUCT.md).

---

Thanks again for contributing! Let's build secure, open agentic workflows – together 🚀
