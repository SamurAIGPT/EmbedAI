---
name: muapi-generate
description: Generate images and videos using muapi.ai's 50+ AI models with async queue support. Use when user requests "Generate image", "Create video", "Text to image", "Text to video", "Image to video", "Make a picture of...", "Upload file", or similar generation tasks.
metadata:
  author: muapi-ai
  version: "1.0.0"
---

# muapi.ai Generate

Generate images and videos using state-of-the-art AI models on muapi.ai.

## Scripts

| Script | Purpose |
|--------|---------|
| `generate-image.sh` | Text-to-image generation |
| `generate-video.sh` | Text-to-video generation (async) |
| `image-to-video.sh` | Image-to-video generation (async) |
| `upload.sh` | Upload local files to muapi CDN |

## API Flow

```
POST /api/v1/{endpoint}     →  {"request_id": "abc123"}
             ↓
GET  /api/v1/predictions/{id}/result  →  poll until completed
             ↓
         {"status": "completed", "outputs": ["https://cdn.muapi.ai/..."]}
```

---

## Generate Image

```bash
bash /mnt/skills/user/muapi-generate/scripts/generate-image.sh [options]
```

### Models (`--model`)

| Flag | Description |
|------|-------------|
| `flux-dev` | Flux Dev — fast, high quality **(default)** |
| `flux-schnell` | Flux Schnell — fastest |
| `flux-kontext-dev` | Flux Kontext Dev T2I |
| `flux-kontext-pro` | Flux Kontext Pro T2I |
| `flux-kontext-max` | Flux Kontext Max T2I |
| `hidream-fast` | HiDream i1 Fast |
| `hidream-dev` | HiDream i1 Dev |
| `hidream-full` | HiDream i1 Full |
| `midjourney` | Midjourney v7 |
| `gpt4o` | GPT-4o image |
| `seedream` | ByteDance Seedream |
| `reve` | Reve |
| `qwen` | Qwen Image |
| `wan` | Wan 2.1 T2I |

### Examples

```bash
# Basic image (waits for completion)
bash generate-image.sh --prompt "A serene mountain landscape at sunrise"

# Midjourney v7
bash generate-image.sh --prompt "cinematic portrait, golden hour" --model midjourney

# GPT-4o with instruction-following
bash generate-image.sh --prompt "A red car with blue wheels on a white background" --model gpt4o

# Custom dimensions
bash generate-image.sh --prompt "a cat" --width 1280 --height 720

# Multiple images
bash generate-image.sh --prompt "abstract art" --num-images 4

# Async (returns request_id immediately)
bash generate-image.sh --prompt "a galaxy" --model hidream-full --async
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--prompt`, `-p` | Text description (required) | — |
| `--model`, `-m` | Model flag (see table above) | `flux-dev` |
| `--width` | Image width in pixels | `1024` |
| `--height` | Image height in pixels | `1024` |
| `--aspect-ratio` | `1:1`, `16:9`, `9:16`, `4:3`, `3:4` | — |
| `--num-images` | Number of images (1–4) | `1` |
| `--async` | Submit and return request_id immediately | — |
| `--timeout` | Max seconds to wait | `300` |
| `--json` | Output raw JSON only | — |

---

## Generate Video (Text-to-Video)

```bash
bash /mnt/skills/user/muapi-generate/scripts/generate-video.sh [options]
```

### Models (`--model`)

| Flag | Description |
|------|-------------|
| `veo3` | Google Veo3 — highest quality |
| `veo3-fast` | Google Veo3 Fast |
| `kling-master` | Kling v2.1 Master T2V |
| `wan2` | Wan 2.1 T2V |
| `wan22` | Wan 2.2 T2V |
| `wan22-fast` | Wan 2.2 5B Fast T2V |
| `seedance-pro` | Seedance Pro T2V |
| `seedance-lite` | Seedance Lite T2V |
| `hunyuan` | HunyuanVideo T2V |
| `hunyuan-fast` | HunyuanVideo Fast T2V |
| `runway` | Runway T2V |
| `pixverse` | Pixverse v4.5 T2V |
| `vidu` | Vidu v2.0 T2V |
| `minimax-std` | Minimax Hailuo-02 Standard T2V |
| `minimax-pro` | Minimax Hailuo-02 Pro T2V **(default)** |

### Examples

```bash
# Default (waits for completion — may take 1–3 mins)
bash generate-video.sh --prompt "ocean waves crashing on a rocky shore"

# Veo3 — highest quality
bash generate-video.sh --prompt "a hummingbird in slow motion" --model veo3

# Kling — fast, reliable
bash generate-video.sh --prompt "a spaceship launching" --model kling-master

# Async mode (recommended for long videos)
bash generate-video.sh --prompt "epic battle scene" --model veo3 --async
# → Request ID: abc123-def456
# → Check: bash check-result.sh --id "abc123-def456"

# 9:16 vertical
bash generate-video.sh --prompt "city timelapse" --model wan2 --aspect-ratio 9:16
```

### Queue Operations

```bash
# Submit and return immediately
bash generate-video.sh --prompt "..." --model veo3 --async

# Check status / get result
bash generate-video.sh --status "request_id" --model veo3
bash generate-video.sh --result "request_id"
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--prompt`, `-p` | Text description (required) | — |
| `--model`, `-m` | Model flag | `minimax-pro` |
| `--aspect-ratio` | `16:9`, `9:16`, `1:1` | `16:9` |
| `--duration` | Duration in seconds (5 or 10) | `5` |
| `--async` | Submit and return request_id immediately | — |
| `--status ID` | Check status of a queued request | — |
| `--result ID` | Get result of a completed request | — |
| `--timeout` | Max seconds to wait | `600` |
| `--json` | Output raw JSON only | — |

---

## Image to Video

```bash
bash /mnt/skills/user/muapi-generate/scripts/image-to-video.sh [options]
```

### Models (`--model`)

| Flag | Description |
|------|-------------|
| `kling-std` | Kling v2.1 Standard I2V |
| `kling-pro` | Kling v2.1 Pro I2V **(best overall)** |
| `kling-master` | Kling v2.1 Master I2V |
| `veo3` | Google Veo3 I2V |
| `veo3-fast` | Google Veo3 Fast I2V |
| `wan2` | Wan 2.1 I2V |
| `wan22` | Wan 2.2 I2V |
| `seedance-pro` | Seedance Pro I2V |
| `seedance-lite` | Seedance Lite I2V |
| `hunyuan` | HunyuanVideo I2V |
| `runway` | Runway I2V |
| `pixverse` | Pixverse v4.5 I2V |
| `vidu` | Vidu v2.0 I2V |
| `midjourney` | Midjourney v7 I2V |
| `minimax-std` | Minimax Hailuo-02 Standard I2V |
| `minimax-pro` | Minimax Hailuo-02 Pro I2V |

### Examples

```bash
# From URL
bash image-to-video.sh \
  --image-url "https://example.com/photo.jpg" \
  --prompt "camera slowly zooms in" \
  --model kling-pro

# From local file (auto-uploaded to CDN)
bash image-to-video.sh \
  --file "/path/to/photo.jpg" \
  --prompt "gentle wind blowing through the scene" \
  --model veo3-fast

# With last frame (start+end interpolation)
bash image-to-video.sh \
  --image-url "https://example.com/start.jpg" \
  --last-image-url "https://example.com/end.jpg" \
  --model kling-pro

# Async
bash image-to-video.sh --image-url "..." --prompt "..." --model veo3 --async
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--image-url` | Input image URL | — |
| `--file`, `--image` | Local file (auto-uploads to CDN) | — |
| `--last-image-url` | End frame URL (for start+end I2V) | — |
| `--prompt`, `-p` | Motion description | `""` |
| `--model`, `-m` | Model flag | `kling-pro` |
| `--aspect-ratio` | `16:9`, `9:16`, `1:1` | `16:9` |
| `--duration` | Duration in seconds (5 or 10) | `5` |
| `--async` | Submit and return request_id immediately | — |
| `--status ID` | Check status | — |
| `--result ID` | Get result | — |
| `--timeout` | Max seconds to wait | `600` |

---

## File Upload

```bash
bash /mnt/skills/user/muapi-generate/scripts/upload.sh --file "/path/to/file.jpg"
```

Returns the CDN URL for use in subsequent requests.

```bash
URL=$(bash upload.sh --file "/path/to/photo.jpg")
# → https://cdn.muapi.ai/files/abc123/photo.jpg

bash image-to-video.sh --image-url "$URL" --prompt "zoom in slowly"
```

**Supported types:** jpg, jpeg, png, gif, webp, mp4, mov, webm, mp3, wav

---

## Output Format

### Image Response
```json
{
  "request_id": "abc123",
  "status": "completed",
  "outputs": ["https://cdn.muapi.ai/files/abc123/image.png"]
}
```

### Video Response
```json
{
  "request_id": "abc123",
  "status": "completed",
  "outputs": ["https://cdn.muapi.ai/files/abc123/video.mp4"]
}
```

## Present Results to User

**Images:**
```
![Generated Image](https://cdn.muapi.ai/files/.../image.png)
• Model: Flux Dev | Generated in 3.2s
```

**Videos:**
```
[View Video](https://cdn.muapi.ai/files/.../video.mp4)
• Model: Kling Pro | Duration: 5s | Generated in 45s
```

**Async Submission:**
```
Request submitted to queue.
• Request ID: abc123-def456
• Model: veo3
• Check: bash check-result.sh --id "abc123-def456"
```

## Troubleshooting

### API Key Error
```
Error: MUAPI_KEY not set
Run: bash setup.sh --add-key "your_key_here"
Or:  export MUAPI_KEY=your_key_here
```

### Timeout
```
Error: Timeout after 600s
Request ID: abc123-def456
Check manually: bash check-result.sh --id "abc123-def456"
```
