# 🎭 Generative Media Skills for AI Agents

**Transform your AI Agent into a domain expert.**  
A scalable architecture for AI coding agents (**Claude Code, Cursor, Gemini CLI**) to access 100+ generative media models through curated expert skills.

---

## 🏗️ Scalable Architecture

This repository uses a **Core/Library** split to ensure efficiency as it scales to 100+ skills:

### ⚙️ Core Primitives (`/core`)
The raw infrastructure for interacting with [muapi.ai](https://muapi.ai).
- `core/media/` — Generation (Image, Video, Audio)
- `core/edit/` — Editing & Enhancement (Lipsync, Upscale)
- `core/platform/` — Setup & Utilities

### 📚 Expert Library (`/library`)
Domain-specific skills that bake in "Expert Knowledge" and "Prompt Engineering."
- `library/visual/nano-banana/` — High-fidelity 3D/Photo art skill.
- *Coming Soon: 99+ additional specialized skills.*

---

## 🚀 Quick Start (Expert Skill)

### 1. Setup API Key
```bash
bash core/platform/setup.sh --add-key "YOUR_MUAPI_KEY"
```

### 2. Run an Expert Skill (Nano-Banana)
```bash
cd library/visual/nano-banana
bash scripts/generate-nano-art.sh --subject "Cyberpunk city in 8k" --style "photorealistic"
```

---

## 🔧 Compatibility
- **Claude Code:** Use via terminal commands.
- **Gemini CLI / Cursor:** Integrated as local tools/scripts.
- **MCP:** Each skill directory is designed to be easily wrapped as a Model Context Protocol tool.

---

## 📄 License
MIT © 2026
