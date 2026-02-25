# 🎭 Generative Media Skills for AI Agents

**The Ultimate Multimodal Toolset for Claude Code, Cursor, and Gemini CLI.**  
A high-performance, schema-driven architecture for AI agents to generate, edit, and display professional-grade images, videos, and audio.

[🚀 Get Started](#-quick-start) | [🎨 Expert Library](#-expert-library) | [⚙️ Core Primitives](#-core-primitives) | [📖 Reference](#-schema-reference)

---

## ✨ Key Features

- **🤖 Agent-Native Design** — Standardized terminal scripts with clean JSON outputs for seamless integration into agentic workflows.
- **🧠 Expert Knowledge Layer** — Domain-specific skills that bake in professional cinematography, atomic design, and branding logic.
- **⚡ Dynamic Schema-Driven** — Powered by `schema_data.json`, scripts automatically resolve the latest models, endpoints, and valid parameters.
- **🖼️ Direct Media Display** — Use the `--view` flag to automatically download and open generated media in your system viewer.
- **🌈 100+ AI Models** — One-click access to **Midjourney v7, Flux Pro, Kling 3.0, Veo3, Suno V5**, and more.

---

## 🏗️ Scalable Architecture

This repository uses a **Core/Library** split to ensure efficiency and high-signal discovery for LLMs:

### ⚙️ Core Primitives (`/core`)
The raw infrastructure for interacting with the [muapi.ai](https://muapi.ai) engine.
- `core/media/` — High-fidelity Generation (Image, Video, Audio)
- `core/edit/` — Advanced Editing (Lipsync, Upscale, Effects)
- `core/platform/` — Setup & Polling Utilities

### 📚 Expert Library (`/library`)
High-value skills that translate creative intent into technical directives.
- **Cinema Director** (`/library/motion/cinema-director/`) — Technical film direction & cinematography.
- **Nano-Banana** (`/library/visual/nano-banana/`) — Reasoning-driven image generation (Gemini 3 Style).
- **UI Designer** (`/library/visual/ui-design/`) — High-fidelity mobile/web mockups (Atomic Design).
- **Logo Creator** (`/library/visual/logo-creator/`) — Minimalist vector branding (Geometric Primitives).

---

## 🧠 Self-Optimizing Skills

Every expert skill in the **Library** includes a **Prompt Optimization Protocol**. This allows LLMs (like Claude or Gemini) to use their own reasoning to expand simple user requests into high-fidelity technical briefs before calling the generation scripts.

---

## 🚀 Quick Start

### 1. Configure Your API Key
```bash
# Get your key at https://muapi.ai/dashboard
bash core/platform/setup.sh --add-key "YOUR_MUAPI_KEY"
```

### 2. Run an Expert Skill with Direct Display
Generate a high-fidelity image and open it immediately using the `--view` flag.
```bash
# Use Nano-Banana reasoning to generate a 2K masterpiece
bash library/visual/nano-banana/scripts/generate-nano-art.sh \
  --subject "a glass hummingbird" \
  --style "macro photography" \
  --resolution "2k" \
  --view
```

### 3. Direct a Cinematic Scene
```bash
cd library/motion/cinema-director
# Create a 10-second 'epic' reveal without audio
bash scripts/generate-film.sh \
  --subject "a cybernetic dragon over Tokyo" \
  --intent "epic" \
  --model "kling-v3.0-pro" \
  --duration 10 \
  --no-audio \
  --view
```

---

## 📖 Schema Reference

This repository includes a streamlined `schema_data.json` that core scripts use at runtime to:
- **Validate Model IDs**: Ensures the requested model exists.
- **Resolve Endpoints**: Automatically maps model names to API endpoints.
- **Check Parameters**: Validates supported `aspect_ratio`, `resolution`, and `duration` values.

---

## 🔧 Compatibility

Optimized for the next generation of AI development environments:
- **Claude Code:** Direct terminal execution via tools.
- **Gemini CLI / Cursor / Windsurf:** Seamless integration as local scripts.
- **MCP:** Each skill is Model Context Protocol-ready for universal agent usage.

---

## 📄 License
MIT © 2026
