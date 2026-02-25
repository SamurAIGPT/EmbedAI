# Generative Media Skills for AI Agents

A collection of high-performance generative media skills for AI coding agents (Claude Code, Cursor, Gemini CLI, Windsurf). Create images, videos, and audio tracks directly from your agent workflow, powered by the [muapi.ai](https://muapi.ai) multi-model engine.

## Compatibility

These skills provide a standardized way to integrate generative media into modern AI agent environments:
- **Claude Code:** Use via terminal commands.
- **Gemini CLI:** Integrated as local tool/scripts.
- **Cursor / Windsurf:** Add to `.cursorrules` or use via terminal.
- **MCP (Model Context Protocol):** Scripts can be easily wrapped as MCP tools.

## Installation

```bash
npx skills add muapi-ai/skills
```

## Available Skills

### muapi-generate

Generate images and videos using 50+ models with async queue support.

**Use when:**
- "Generate an image of..."
- "Create a video"
- "Make a picture using Midjourney / Flux / Seedream"
- "Text to video with Veo3 / Kling / Wan"
- "Image to video"
- "Upload a file"

**Scripts:**
- `generate-image.sh` — Text-to-image (Flux, Midjourney, GPT-4o, HiDream, Seedream, Reve, Qwen, Wan)
- `generate-video.sh` — Text-to-video (Veo3, Kling, Wan, Seedance, Runway, Hunyuan, Minimax, Pixverse, Vidu)
- `image-to-video.sh` — Image-to-video (same models, I2V variants)
- `upload.sh` — Upload local files to muapi CDN

### muapi-image-edit

Edit and enhance images using AI.

**Use when:**
- "Edit this image"
- "Change the style / background"
- "Upscale / enhance this photo"
- "Remove the background / object"
- "Face swap"
- "Make it Ghibli style / anime"

**Scripts:**
- `edit-image.sh` — Flux Kontext i2i, GPT-4o edit, Reve, SeedEdit, Midjourney, Qwen
- `enhance-image.sh` — Upscale, background remove, face swap, style transfer, object eraser

### muapi-video-edit

Edit videos with AI effects, lipsync, and transformations.

**Use when:**
- "Add lipsync to this video"
- "Apply AI effects to video"
- "Dance effects"
- "Modify this video"

**Scripts:**
- `video-effects.sh` — Wan effects, video/image effects, dance, face swap, dress change, Luma
- `lipsync.sh` — Sync, LatentSync, Creatify, Veed lipsync

### muapi-audio

Music and audio generation.

**Use when:**
- "Create music"
- "Generate a song"
- "Add audio to video"
- "Extend / remix this track"

**Scripts:**
- `create-music.sh` — Suno create/remix/extend, MMAudio text-to-audio, video-to-audio

### muapi-platform

Platform utilities: API key setup, file upload, prediction polling.

**Use when:**
- "Setup my API key"
- "Check result of request"
- "Upload a file"

**Scripts:**
- `setup.sh` — Configure MUAPI_KEY
- `check-result.sh` — Poll a prediction by request ID

---

## Getting Your API Key

1. Go to [muapi.ai/dashboard](https://muapi.ai/dashboard)
2. Navigate to API Keys
3. Create a new key

## Quick Start

### 1. Setup API Key

```bash
bash setup.sh --add-key "your_key_here"
# Or: export MUAPI_KEY=your_key_here
```

### 2. Generate an Image

```bash
# Flux Dev (fast, high quality)
bash generate-image.sh --prompt "a sunset over mountains" --model flux-dev

# Midjourney v7
bash generate-image.sh --prompt "cinematic portrait" --model midjourney

# GPT-4o image
bash generate-image.sh --prompt "a futuristic city" --model gpt4o
```

### 3. Generate a Video

```bash
# Text-to-video with Veo3
bash generate-video.sh --prompt "ocean waves crashing" --model veo3

# Async mode (returns request_id immediately for long jobs)
bash generate-video.sh --prompt "epic battle scene" --model kling-master --async

# Check status / get result
bash check-result.sh --id "request_id_here"
```

### 4. Image to Video

```bash
bash image-to-video.sh --image-url "https://example.com/photo.jpg" \
  --prompt "camera slowly zooms in" --model kling-pro
```

### 5. Music Generation

```bash
bash create-music.sh --style "lo-fi hip hop" --prompt "chill beats for studying"
```

---

## API Reference

**Base URL:** `https://api.muapi.ai/api/v1`

**Authentication:** `x-api-key: YOUR_KEY` header

**Request flow:**
```
POST /api/v1/{endpoint}  →  {"request_id": "abc123"}
GET  /api/v1/predictions/{id}/result  →  poll until {"status": "completed", "outputs": [...]}
```

## Common Flags

All scripts support:

| Flag | Description |
|------|-------------|
| `--add-key [KEY]` | Save MUAPI_KEY to .env |
| `--help`, `-h` | Show help |
| `--json` | Raw JSON output only |
| `--async` | Submit and return request_id immediately |
| `--status ID` | Check status of a queued request |

## Recommended Models

### Text-to-Image

| Model Flag | API Endpoint | Notes |
|------------|--------------|-------|
| `flux-dev` | `flux-dev-image` | Fast, high quality |
| `flux-schnell` | `flux-schnell-image` | Fastest |
| `midjourney` | `midjourney-v7-text-to-image` | Best artistic quality |
| `gpt4o` | `gpt4o-text-to-image` | Instruction-following |
| `seedream` | `bytedance-seedream-image` | Photorealistic |
| `hidream-fast` | `hidream_i1_fast_image` | Fast, detailed |
| `reve` | `reve-text-to-image` | Creative styles |
| `qwen` | `qwen-image` | Good prompt adherence |
| `wan` | `wan2.1-text-to-image` | Open source |
| `flux-kontext-pro` | `flux-kontext-pro-t2i` | Best Kontext |

### Text-to-Video

| Model Flag | API Endpoint | Notes |
|------------|--------------|-------|
| `veo3` | `veo3-text-to-video` | Highest quality |
| `veo3-fast` | `veo3-fast-text-to-video` | Fast version |
| `kling-master` | `kling-v2.1-master-t2v` | Best overall |
| `wan2` | `wan2.1-text-to-video` | Open source |
| `wan22` | `wan2.2-text-to-video` | Latest Wan |
| `seedance-pro` | `seedance-pro-t2v` | Fast, good quality |
| `hunyuan` | `hunyuan-text-to-video` | High quality |
| `runway` | `runway-text-to-video` | Creative |
| `minimax-pro` | `minimax-hailuo-02-pro-t2v` | Good for characters |
| `pixverse` | `pixverse-v4.5-t2v` | Creative styles |

### Image-to-Video

| Model Flag | API Endpoint | Notes |
|------------|--------------|-------|
| `kling-pro` | `kling-v2.1-pro-i2v` | **Best overall** |
| `kling-master` | `kling-v2.1-master-i2v` | Premium |
| `veo3` | `veo3-image-to-video` | High quality |
| `veo3-fast` | `veo3-fast-image-to-video` | Fast |
| `seedance-pro` | `seedance-pro-i2v` | Smooth motion |
| `wan2` | `wan2.1-image-to-video` | Open source |
| `minimax-pro` | `minimax-hailuo-02-pro-i2v` | Good characters |
| `runway` | `runway-image-to-video` | Creative |
| `pixverse` | `pixverse-v4.5-i2v` | Creative |
| `vidu` | `vidu-v2.0-i2v` | Fast |

## License

MIT
