# 🎭 Generative Media Skills for AI Agents

**The Ultimate Multimodal Toolset for Claude Code, Cursor, and Gemini CLI.**  
A high-performance, schema-driven architecture for AI agents to generate, edit, and display professional-grade images, videos, and audio.

![Agent Skills Demo](media_outputs/cld6-demo.webp)

[🚀 Get Started](#-quick-start) | [🎨 Expert Library](#-expert-library) | [⚙️ Core Primitives](#-core-primitives) | [📖 Reference](#-schema-reference)

---

## ✨ Key Features

- **🤖 Agent-Native Design** — Standardized terminal scripts with clean JSON outputs for seamless integration into agentic workflows.
- **🧠 Expert Knowledge Layer** — Domain-specific skills that bake in professional cinematography, atomic design, and branding logic.
- **⚡ Dynamic Schema-Driven** — Powered by `schema_data.json`, scripts automatically resolve the latest models, endpoints, and valid parameters.
- **🖼️ Direct Media Display** — Use the `--view` flag to automatically download and open generated media in your system viewer.
- **📁 Local File Support** — Auto-upload images, videos, faces, and audio from your local machine to the CDN for processing.
- **🌈 100+ AI Models** — One-click access to **Midjourney v7, Flux Pro, Seedance 2.0, Kling 3.0, Veo3**, and more.

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
- **Seedance 2 (Doubao Video)** (`/library/motion/seedance-2/`) — Director-level cinematic video generation with native audio-video sync (ByteDance).

---

## 🧠 Self-Optimizing Skills

Every expert skill in the **Library** includes a **Prompt Optimization Protocol**. This allows AI agents to translate simple user requests into high-fidelity technical briefs (e.g. converting "cool city shot" into a **Seedance 2** technical director brief with dolly motion and rim lighting).

---

## 🚀 Quick Start

### 1. Install the Skills
```bash
# Install all skills to your AI agent
npx skills add SamurAIGPT/Generative-Media-Skills --all

# Or install a specific skill
npx skills add SamurAIGPT/Generative-Media-Skills --skill muapi-media-generation

# List available skills
npx skills add SamurAIGPT/Generative-Media-Skills --list

# Install to specific agents
npx skills add SamurAIGPT/Generative-Media-Skills --all -a claude-code -a cursor
```

### 2. Configure Your API Key
```bash
# Get your key at https://muapi.ai/dashboard
bash core/platform/setup.sh --add-key "YOUR_MUAPI_KEY"
```

### 3. Run an Expert Skill with Direct Display
Generate a high-fidelity image and open it immediately using the `--view` flag.
```bash
# Use Nano-Banana reasoning to generate a 2K masterpiece from a local image
bash library/visual/nano-banana/scripts/generate-nano-art.sh \
  --file ./my-source-image.jpg \
  --subject "a glass hummingbird" \
  --style "macro photography" \
  --resolution "2k" \
  --view
```

### 4. Direct a Cinematic Scene
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

# Direct a cinematic masterpiece with Seedance 2
bash library/motion/seedance-2/scripts/generate-seedance.sh \
  --subject "a floating steampunk city" \
  --intent "reveal" \
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
