# BugHunterClaudex 🐛

**An AI-powered workspace for learning cybersecurity, researching bugs, and testing systems you have permission to test.**

BugHunterClaudex brings an AI assistant, a learning library, and practical research tools into one desktop app. The goal is simple: make it easier to study how security issues work, investigate them carefully, and keep useful evidence in one place.

> **🚧 Work in progress:** This is a personal project under active development. Some tools are experimental, require separate setup, or have not been fully tested on real systems. It is not a finished, one-click hacking tool.

## 🤝 Help wanted — developers and ethical hackers

**I'm looking for help from fellow developers, ethical hackers, and security researchers to make BugHunterClaudex more robust, reliable, and useful.**

One of my biggest challenges has been building cybersecurity features with LLMs. AI assistants sometimes flag or refuse security-related prompts, even when I'm working on legitimate research and testing features. This has slowed development, and I haven't been able to complete or fully verify everything I planned.

If you have experience with **Python, React, Rust, macOS apps, AI agents, web security, reverse engineering, or testing**, I'd really appreciate your help. Code reviews, bug reports, fixes, better documentation, testing, and pull requests are all welcome.

**You don't need to be an expert to contribute.** If you see something broken or have an idea to improve it, please open an issue or a pull request.

## ✨ What it can do

- **AI assistant:** Chat with a connected model and use it to help organize research and understand technical material.
- **Multiple AI options:** Connect supported ChatGPT/Codex and Claude Code subscriptions, paid APIs, or local models through Ollama.
- **Learning library:** Import documents, code, websites, and supported video material. Search useful passages with references to their sources.
- **Web and API testing:** Review websites and HTTP requests within a defined, authorized scope. Save observations and supporting evidence.
- **Burp Suite and browser tools:** Work with captured traffic and controlled browser sessions; some integrations need separate installation.
- **Local memory research:** Explore local process-memory workflows using supported tools, with operating-system and permission limits.
- **Research workspaces:** Explore optional Tor research, desktop automation, and experimental game-analysis modules.
- **Reports and exports:** Keep findings, notes, citations, and investigation results together for later review.

**Important:** Importing courses adds searchable knowledge; it does **not** retrain an AI model. And an AI suggestion is not proof of a vulnerability — findings must be tested and verified.

## 🛠️ Built with

- **Frontend:** React, TypeScript, Vite
- **Desktop app:** Tauri, Rust
- **Backend:** Python
- **Local storage:** SQLite
- **AI connections:** Claude Code, Codex CLI, OpenAI-compatible APIs, Ollama
- **Optional tools:** Playwright, Burp Suite, FFmpeg, Tesseract, and other research utilities

## 🚀 Getting started (macOS)

The current desktop setup is aimed at **macOS**. You'll need an internet connection, and the installer may ask you to approve Apple developer tools or other dependencies.

### 1. Clone the project

```bash
git clone https://github.com/rkstlohchab/bughunter.git
cd bughunter
```

### 2. Run the setup script

```bash
bash setup-mac.sh
```

The script prepares the main dependencies and attempts to set up optional tools. It may take additional manual steps for Burp Suite, model access, and macOS privacy permissions.

### 3. Build the desktop app

```bash
npm run desktop:build
```

After a successful build, you can find the app at:

```text
src-tauri/target/release/bundle/macos/BugHunterClaudex.app
```

### 4. Connect an AI model

Open the app's **Settings**, choose a supported AI provider or local model, and configure any required login or API connection. Then you can start with **Chat**, import material into **Library**, or open a research workspace.

For more detailed instructions, see [Mac setup](ONE_COMMAND_MAC_SETUP.md), [tool connections](docs/setup.md), and [AI connections](docs/ai-connections.md).

## 🧭 How to use it

1. **Ask:** Open Chat and describe what you want to learn or investigate.
2. **Learn:** Add notes, courses, documents, or supported videos to the Library.
3. **Investigate:** Create a project, set its scope, and use the available research tools.
4. **Review:** Check the evidence and approve requests that need permission.
5. **Save:** Export your notes and findings so you can revisit them later.

For a safe first walkthrough, use the **disposable test shop** included with the project. It lets you practice evidence-based testing without involving a real store.

## 🧪 Current status and limitations

BugHunterClaudex combines several tools, but **not every feature is complete or independently verified**.

- Real-world testing depends on the tools, models, permissions, and operating system available on your machine.
- Local models may struggle with long or complex tasks, especially with limited RAM.
- Some memory, game-analysis, desktop-control, and specialized integrations are experimental or platform-specific.
- The app cannot guarantee that it will find every bug or complete an investigation automatically.
- All testing must stay within an authorized scope. Do not use it against systems without permission.

See [validation and known limits](docs/validation.md) for more precise test results.

## 🗺️ What I'd like to improve

- Make the AI workflows more reliable and easier to use.
- Improve error handling, testing, and the stability of tool connections.
- Make setup and troubleshooting simpler.
- Improve documentation and the quality of reports.
- Finish and validate experimental features before calling them production-ready.

## 💬 Contributing

This project is open to ideas, feedback, and code contributions.

1. Check the [Issues](https://github.com/rkstlohchab/bughunter/issues) page or open a new issue describing the problem or improvement.
2. If you'd like to fix something, fork the repo and make a focused change.
3. Submit a pull request explaining what changed and how you tested it.

Please avoid including API keys, passwords, private user data, or sensitive real-world targets in issues and pull requests.

**If you're a developer or ethical hacker who wants to help build this, I'd love to collaborate.** Even small fixes help move the project forward.

## 📚 More documentation

- [One-command Mac setup](ONE_COMMAND_MAC_SETUP.md)
- [AI connections](docs/ai-connections.md)
- [Knowledge and learning](docs/knowledge.md)
- [Security testing and execution limits](docs/execution.md)
- [Research validation](docs/validation.md)
- [GameLab status](docs/game-lab/README.md)

---

**Made for learning, research, and responsible security testing.** If you find the project interesting, consider starring it or contributing. ⭐
