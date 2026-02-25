# 🎭 Generative Media Skills for AI Agents

**Transform your AI Agent into a creative powerhouse.**  
A standardized collection of high-performance skills for AI coding agents (**Claude Code, Cursor, Gemini CLI, Windsurf**) to generate, edit, and enhance images, videos, and audio.

[🚀 Get Started](#quick-start) | [🎨 Supported Models](#recommended-models) | [📖 API Reference](#api-reference)

---

## ✨ Features

- **🌈 Multimodal Mastery** — Generate high-fidelity Images, Videos, and Audio tracks within a single workflow.
- **🤖 Agent-Native** — Optimized for terminal-based agents with clean JSON outputs and async job handling.
- **🛠️ 100+ AI Models** — One integration to access **Midjourney v7, Flux, Kling, Veo3, Suno,** and more.
- **⚡ Pro-Grade Controls** — Support for upscaling, background removal, face swapping, and cinematic motion.
- **🔌 Powered by [muapi.ai](https://muapi.ai)** — A unified generative media engine.

---

## 🏗️ Architecture

```text
/
├── 🖼️ muapi-generate/      # Text-to-Image, Text-to-Video, I2V
├── 🪄 muapi-image-edit/    # Upscaling, Style Transfer, Inpainting
├── 🎬 muapi-video-edit/    # Lipsync, Video Effects, Face Swap
├── 🎵 muapi-audio/         # Music & Sound Generation
└── ⚙️ muapi-platform/      # API Setup & Result Polling
```

---

## 🚀 Quick Start

### 1. Setup API Key
```bash
# Get your key at https://muapi.ai/dashboard
bash muapi-platform/scripts/setup.sh --add-key "YOUR_MUAPI_KEY"
```

### 2. Generate Your First Masterpiece
```bash
# Generate a cinematic image with Flux
bash muapi-generate/scripts/generate-image.sh --prompt "Cyberpunk city in 8k" --model flux-dev

# Create a high-quality video with Veo3
bash muapi-generate/scripts/generate-video.sh --prompt "Drone shot of ocean waves" --model veo3
```

---

## 🎨 Supported Models

### 🖼️ Image Generation
| Model | API Endpoint | Highlights |
| :--- | :--- | :--- |
| **Midjourney v7** | `midjourney-v7-t2i` | Best artistic quality & realism |
| **Flux Dev** | `flux-dev-image` | High prompt adherence & detail |
| **GPT-4o** | `gpt4o-text-to-image` | Excellent instruction following |
| **Seedream** | `bytedance-seedream` | Photorealistic textures |

### 🎥 Video Generation
| Model | API Endpoint | Highlights |
| :--- | :--- | :--- |
| **Veo3** | `veo3-text-to-video` | Industry-leading video quality |
| **Kling Master** | `kling-v2.1-master` | Premium motion & consistency |
| **Wan 2.1** | `wan2.1-text-to-video` | State-of-the-art open source |
| **Luma Dream** | `luma-dream-machine` | Creative cinematic motion |

---

## 🔧 Compatibility

These skills are "plug-and-play" for modern AI agent environments:
- **Claude Code:** Direct terminal execution.
- **Gemini CLI:** Integrated as local scripts/tools.
- **Cursor / Windsurf:** Add to `.cursorrules` or terminal.
- **MCP:** Easily wrappable as Model Context Protocol tools.

---

## 📖 API Reference

**Base URL:** `https://api.muapi.ai/api/v1`  
**Auth:** `x-api-key: YOUR_KEY`

**Job Flow:**
1. **Submit:** `POST /api/v1/{endpoint}` → returns `request_id`
2. **Poll:** `GET /api/v1/predictions/{id}/result` → wait for `completed`

---

## 📄 License

MIT © 2026
