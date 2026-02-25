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
- `library/motion/cinema-director/` — Technical film direction & cinematography.
- `library/visual/nano-banana/` — Reasoning-driven image generation (Gemini 3 Style).
- `library/visual/ui-design/` — Mobile/Web UI mockups (Atomic Design).
- `library/visual/logo-creator/` — Minimalist vector branding (Geometric Primitives).

---

## 🚀 Quick Start (Expert Directed Film)

### 1. Setup API Key
```bash
bash core/platform/setup.sh --add-key "YOUR_MUAPI_KEY"
```

### 2. Direct a Cinematic Scene
The Cinema Director skill translates "Director's Intent" into technical cinematography.
```bash
cd library/motion/cinema-director
# Direct an 'epic' reveal of a futuristic city
bash scripts/generate-film.sh --subject "a cyberpunk metropolis in the clouds" --intent "epic" --model "kling-master"
```

### 3. Check Result
```bash
bash ../../../core/platform/check-result.sh --id "REQUEST_ID_FROM_STEP_2"
```

---

## 🔧 Compatibility
- **Claude Code:** Use via terminal commands.
- **Gemini CLI / Cursor:** Integrated as local tools/scripts.
- **MCP:** Each skill directory is designed to be easily wrapped as a Model Context Protocol tool.

---

## 📄 License
MIT © 2026
