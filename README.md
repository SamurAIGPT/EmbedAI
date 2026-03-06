# 🎭 Generative Media Skills for AI Agents

**The Ultimate Multimodal Toolset for Claude Code, Cursor, and Gemini CLI.**
A high-performance, schema-driven architecture for AI agents to generate, edit, and display professional-grade images, videos, and audio — powered by the [muapi-cli](https://github.com/SamurAIGPT/muapi-cli).


[🚀 Get Started](#-quick-start) | [🎨 Expert Library](#-expert-library) | [⚙️ Core Primitives](#-core-primitives) | [🤖 MCP Server](#-mcp-server) | [📖 Reference](#-schema-reference)

---

## ✨ Key Features

- **🤖 Agent-Native Design** — CLI-powered scripts with structured JSON outputs, semantic exit codes, and `--jq` filtering for seamless agentic pipelines.
- **🧠 Expert Knowledge Layer** — Domain-specific skills that bake in professional cinematography, atomic design, and branding logic.
- **⚡ CLI-Powered Core** — All primitives delegate to [`muapi-cli`](https://www.npmjs.com/package/muapi-cli) — no curl, no JSON parsing, no boilerplate.
- **🖼️ Direct Media Display** — Use the `--view` flag to automatically download and open generated media in your system viewer.
- **📁 Local File Support** — Auto-upload images, videos, faces, and audio from your local machine to the CDN for processing.
- **🌈 100+ AI Models** — One-click access to **Midjourney v7, Flux Kontext, Seedance 2.0, Kling 3.0, Veo3**, and more.
- **🔌 MCP Server** — Run `muapi mcp serve` to expose all 19 tools directly to Claude Desktop, Cursor, or any MCP-compatible agent.

---

## 🏗️ Scalable Architecture

This repository uses a **Core/Library** split to ensure efficiency and high-signal discovery for LLMs:

### ⚙️ Core Primitives (`/core`)
Thin wrappers around [`muapi-cli`](https://github.com/SamurAIGPT/muapi-cli) for raw API access.
- `core/media/` — File upload
- `core/edit/` — Image editing (prompt-based)
- `core/platform/` — Setup, auth & result polling

### 📚 Expert Library (`/library`)
High-value skills that translate creative intent into technical directives.
- **Cinema Director** (`/library/motion/cinema-director/`) — Technical film direction & cinematography.
- **Nano-Banana** (`/library/visual/nano-banana/`) — Reasoning-driven image generation (Gemini 3 Style).
- **UI Designer** (`/library/visual/ui-design/`) — High-fidelity mobile/web mockups (Atomic Design).
- **Logo Creator** (`/library/visual/logo-creator/`) — Minimalist vector branding (Geometric Primitives).
- **Seedance 2 (Doubao Video)** (`/library/motion/seedance-2/`) — Director-level cinematic video generation with text-to-video, image-to-video, and video extension with native audio-video sync.

---

## 🚀 Quick Start

### 1. Install the muapi CLI

The core scripts require [`muapi-cli`](https://www.npmjs.com/package/muapi-cli). Install it once:

```bash
# via npm (recommended — no Python required)
npm install -g muapi-cli

# via pip
pip install muapi-cli

# or run without installing
npx muapi-cli --help
```

### 2. Configure Your API Key

```bash
# Interactive setup
muapi auth configure

# Or pass directly
muapi auth configure --api-key "YOUR_MUAPI_KEY"

# Get your key at https://muapi.ai/dashboard
```

### 3. Install the Skills

```bash
# Install all skills to your AI agent
npx skills add SamurAIGPT/Generative-Media-Skills --all

# Or install a specific skill
npx skills add SamurAIGPT/Generative-Media-Skills --skill muapi-media-generation

# Install to specific agents
npx skills add SamurAIGPT/Generative-Media-Skills --all -a claude-code -a cursor
```

### 4. Generate Your First Image

```bash
muapi image generate "a cyberpunk city at night" --model flux-dev

# Download the result automatically
muapi image generate "a sunset over mountains" --model hidream-fast --download ./outputs

# Extract just the URL (agent-friendly)
muapi image generate "product on white bg" --model flux-schnell --output-json --jq '.outputs[0]'
```

### 5. Run an Expert Skill

```bash
# Use Nano-Banana reasoning to generate a 2K masterpiece
bash library/visual/nano-banana/scripts/generate-nano-art.sh \
  --file ./my-source-image.jpg \
  --subject "a glass hummingbird" \
  --style "macro photography" \
  --resolution "2k" \
  --view
```

### 6. Direct a Cinematic Scene

```bash
cd library/motion/cinema-director

# Create a 10-second epic reveal
bash scripts/generate-film.sh \
  --subject "a cybernetic dragon over Tokyo" \
  --intent "epic" \
  --model "kling-v3.0-pro" \
  --duration 10 \
  --view

# Animate a reference image into video
bash library/motion/seedance-2/scripts/generate-seedance.sh \
  --mode i2v \
  --file ./concept.jpg \
  --subject "camera slowly pulls back to reveal the full landscape" \
  --intent "reveal" \
  --view

# Extend an existing video
bash library/motion/seedance-2/scripts/generate-seedance.sh \
  --mode extend \
  --request-id "YOUR_REQUEST_ID" \
  --subject "camera continues pulling back to reveal the vast city" \
  --duration 10
```

---

## 🤖 MCP Server

Run muapi as a **Model Context Protocol server** so Claude Desktop, Cursor, or any MCP-compatible agent can call generation tools directly — no shell scripts needed.

```bash
muapi mcp serve
```

**Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "muapi": {
      "command": "muapi",
      "args": ["mcp", "serve"],
      "env": { "MUAPI_API_KEY": "your-key-here" }
    }
  }
}
```

This exposes **19 structured tools** with full JSON Schema input/output definitions:

| Tool | Description |
|------|-------------|
| `muapi_image_generate` | Text-to-image (14 models) |
| `muapi_image_edit` | Image-to-image editing (11 models) |
| `muapi_video_generate` | Text-to-video (13 models) |
| `muapi_video_from_image` | Image-to-video (16 models) |
| `muapi_audio_create` | Music generation (Suno) |
| `muapi_audio_from_text` | Sound effects (MMAudio) |
| `muapi_enhance_upscale` | AI upscaling |
| `muapi_enhance_bg_remove` | Background removal |
| `muapi_enhance_face_swap` | Face swap image/video |
| `muapi_enhance_ghibli` | Ghibli style transfer |
| `muapi_edit_lipsync` | Lip sync to audio |
| `muapi_edit_clipping` | AI highlight extraction |
| `muapi_predict_result` | Poll prediction status |
| `muapi_upload_file` | Upload local file → URL |
| `muapi_keys_list` | List API keys |
| `muapi_keys_create` | Create API key |
| `muapi_keys_delete` | Delete API key |
| `muapi_account_balance` | Get credit balance |
| `muapi_account_topup` | Add credits (Stripe checkout) |

---

## ⚡ Agentic Pipeline Examples

```bash
# Submit async, capture request_id, poll when ready
REQUEST_ID=$(muapi video generate "a dog running on a beach" \
  --model kling-master --no-wait --output-json --jq '.request_id' | tr -d '"')

# ... do other work ...

muapi predict wait "$REQUEST_ID" --download ./outputs

# Pipe a prompt from another command
generate_prompt | muapi image generate - --model flux-dev

# Chain: upload → edit → download
URL=$(muapi upload file ./photo.jpg --output-json --jq '.url' | tr -d '"')
muapi image edit "make it look like a painting" --image "$URL" \
  --model flux-kontext-pro --download ./outputs
```

---

## 📖 Schema Reference

This repository includes a streamlined `schema_data.json` that core scripts use at runtime to:
- **Validate Model IDs**: Ensures the requested model exists.
- **Resolve Endpoints**: Automatically maps model names to API endpoints.
- **Check Parameters**: Validates supported `aspect_ratio`, `resolution`, and `duration` values.

Discover all available models via the CLI:

```bash
muapi models list
muapi models list --category video --output-json
```

---

## 🔧 Compatibility

Optimized for the next generation of AI development environments:
- **Claude Code** — Direct terminal execution via tools + MCP server mode.
- **Gemini CLI / Cursor / Windsurf** — Seamless integration as local scripts.
- **MCP** — Full Model Context Protocol server with typed input/output schemas.
- **CI/CD** — `--output-json`, `--jq`, semantic exit codes for scripting.

---

## 📄 License
MIT © 2026
