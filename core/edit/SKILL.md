---
name: muapi-media-editing
description: Edit and enhance images and videos with AI via muapi.ai — prompt-based editing, upscaling, background removal, face swap, lipsync, video effects, and more
---

# ✏️ MuAPI Media Editing & Enhancement

**Advanced editing and enhancement operations for images and videos.**

Apply AI-powered edits, enhancements, and effects to existing media. Supports prompt-based editing with Flux Kontext, GPT-4o, and Midjourney, plus one-click operations like upscaling and background removal.

## Available Scripts

| Script | Description |
| :--- | :--- |
| `edit-image.sh` | Prompt-based image editing (Flux Kontext, GPT-4o, Midjourney, Qwen, and more) |
| `enhance-image.sh` | One-click operations: upscale, background removal, face swap, colorize, Ghibli style, product shots |
| `lipsync.sh` | Sync video lip movement to audio (Sync Labs, LatentSync, Creatify, Veed) |
| `video-effects.sh` | Video/image effects: Wan AI, face swap, dance, dress change, Luma modify/reframe |

## Quick Start

```bash
# Edit an image with a prompt
bash edit-image.sh --image-url "https://..." --prompt "add sunglasses" --model flux-kontext-pro

# Upscale an image
bash enhance-image.sh --op upscale --image-url "https://..."

# Remove background
bash enhance-image.sh --op background-remove --image-url "https://..."

# Lipsync a video
bash lipsync.sh --video-url "https://..." --audio-url "https://..." --model sync

# Apply dance effect
bash video-effects.sh --op dance --image-url "https://..." --audio-url "https://..."
```

## Common Flags

All scripts support: `--async`, `--json`, `--timeout N`, `--help`

## Requirements

- `MUAPI_KEY` environment variable (set via `core/platform/setup.sh`)
- `curl`, `jq`, `python3`
