---
name: muapi-media-generation
description: Generate AI images, videos, music, and audio from the terminal via muapi.ai — supports 100+ models including Flux, Midjourney v7, Kling 3.0, Veo3, and Suno V5
---

# 🎨 MuAPI Media Generation

**Schema-driven generation primitives for images, videos, and audio.**

Generate professional-grade media directly from the terminal using 100+ state-of-the-art AI models. All scripts are powered by `schema_data.json` for dynamic model and endpoint resolution.

## Available Scripts

| Script | Description | Default Model |
| :--- | :--- | :--- |
| `generate-image.sh` | Text-to-image generation | `flux-dev` |
| `generate-video.sh` | Text-to-video generation | `minimax-pro` |
| `image-to-video.sh` | Animate a static image into video | `kling-pro` |
| `create-music.sh` | Music creation, remix, extend, text/video-to-audio | Suno V5 |
| `upload.sh` | Upload local files to CDN for use with other skills | — |

## Quick Start

```bash
# Generate an image
bash generate-image.sh --prompt "a sunset over mountains" --model flux-dev --view

# Generate a video
bash generate-video.sh --prompt "ocean waves at golden hour" --model minimax-pro --view

# Animate an image
bash image-to-video.sh --image-url "https://..." --prompt "camera slowly pans right" --model kling-pro

# Create music
bash create-music.sh --style "lo-fi hip hop" --prompt "chill beats for studying"

# Upload a local file
bash upload.sh --file ./my-image.jpg
```

## Common Flags

All scripts support: `--async`, `--view`, `--json`, `--timeout N`, `--help`

## Requirements

- `MUAPI_KEY` environment variable (set via `core/platform/setup.sh`)
- `curl`, `jq`, `python3`
