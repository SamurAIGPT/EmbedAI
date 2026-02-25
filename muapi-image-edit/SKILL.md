---
name: muapi-image-edit
description: Edit and enhance images using muapi.ai AI models. Use when user requests "Edit this image", "Change background", "Upscale photo", "Remove background/object", "Face swap", "Make it Ghibli/anime style", "Enhance image quality", "Product shot", "Colorize photo", or similar image editing tasks.
metadata:
  author: muapi-ai
  version: "1.0.0"
---

# muapi.ai Image Edit

Edit, enhance, and transform images using state-of-the-art AI models.

## Scripts

| Script | Purpose |
|--------|---------|
| `edit-image.sh` | AI-powered image editing with text prompts (Flux Kontext, GPT-4o, Reve, SeedEdit, Midjourney) |
| `enhance-image.sh` | One-click image enhancements (upscale, background remove, face swap, style transfer, effects) |

---

## Edit Image (Prompt-based)

```bash
bash /mnt/skills/user/muapi-image-edit/scripts/edit-image.sh [options]
```

Apply text-prompt-based edits to an existing image.

### Models (`--model`)

| Flag | Endpoint | Description |
|------|----------|-------------|
| `flux-kontext-dev` | `flux-kontext-dev-i2i` | Flux Kontext Dev — fast editing |
| `flux-kontext-pro` | `flux-kontext-pro-i2i` | Flux Kontext Pro — best quality **(default)** |
| `flux-kontext-max` | `flux-kontext-max-i2i` | Flux Kontext Max — highest fidelity |
| `flux-kontext-effects` | `flux-kontext-effects` | Named style effects |
| `gpt4o` | `gpt4o-image-to-image` | GPT-4o image edit |
| `gpt4o-edit` | `gpt4o-edit` | GPT-4o edit with mask |
| `reve` | `reve-image-edit` | Reve image edit |
| `seededit` | `bytedance-seededit-image` | ByteDance SeedEdit |
| `midjourney` | `midjourney-v7-image-to-image` | Midjourney v7 I2I |
| `midjourney-style` | `midjourney-v7-style-reference` | Style reference |
| `midjourney-omni` | `midjourney-v7-omni-reference` | Omni reference |
| `qwen` | `qwen-image-edit` | Qwen image edit |

### Examples

```bash
# Change outfit color
bash edit-image.sh \
  --image-url "https://example.com/photo.jpg" \
  --prompt "change the shirt to red" \
  --model flux-kontext-pro

# Remove background
bash edit-image.sh \
  --image-url "https://example.com/photo.jpg" \
  --prompt "remove the background, white background"

# Style transfer with GPT-4o
bash edit-image.sh \
  --image-url "https://example.com/portrait.jpg" \
  --prompt "make it look like a painting in Van Gogh style" \
  --model gpt4o

# Apply a named Kontext effect
bash edit-image.sh \
  --image-url "https://example.com/photo.jpg" \
  --effect "3d-animation" \
  --model flux-kontext-effects

# From local file
bash edit-image.sh \
  --file "/path/to/photo.jpg" \
  --prompt "add sunglasses"
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--image-url` | Source image URL | — |
| `--file` | Local image file (auto-uploads) | — |
| `--prompt`, `-p` | Edit instruction | — |
| `--effect` | Named effect for `flux-kontext-effects` | — |
| `--model`, `-m` | Model (see table) | `flux-kontext-pro` |
| `--aspect-ratio` | `1:1`, `16:9`, `9:16`, `4:3`, `3:4` | `1:1` |
| `--num-images` | 1–4 | `1` |
| `--async` | Return request_id immediately | — |
| `--timeout` | Max wait seconds | `300` |
| `--json` | Raw JSON output | — |

---

## Enhance Image (One-click)

```bash
bash /mnt/skills/user/muapi-image-edit/scripts/enhance-image.sh [options]
```

One-click image enhancements — no prompt required for most operations.

### Operations (`--op`)

| Flag | Endpoint | Description |
|------|----------|-------------|
| `upscale` | `ai-image-upscale` | Upscale image resolution |
| `background-remove` | `ai-background-remover` | Remove background |
| `face-swap` | `ai-image-face-swap` | Swap face (requires `--face-url`) |
| `skin-enhance` | `ai-skin-enhancer` | Smooth and enhance skin |
| `colorize` | `ai-color-photo` | Colorize black & white photo |
| `ghibli` | `ai-ghibli-style` | Studio Ghibli art style |
| `anime` | `ai-anime-generator` | Anime style (with optional prompt) |
| `extend` | `ai-image-extension` | Extend/outpaint image |
| `product-shot` | `ai-product-shot` | Clean product shot |
| `product-photo` | `ai-product-photography` | Professional product photography |
| `object-erase` | `ai-object-eraser` | Erase object (requires `--mask-url`) |

### Examples

```bash
# Upscale an image
bash enhance-image.sh --op upscale --image-url "https://example.com/photo.jpg"

# Remove background
bash enhance-image.sh --op background-remove --image-url "https://example.com/photo.jpg"

# Face swap
bash enhance-image.sh \
  --op face-swap \
  --image-url "https://example.com/target.jpg" \
  --face-url "https://example.com/face.jpg"

# Ghibli style
bash enhance-image.sh --op ghibli --image-url "https://example.com/landscape.jpg"

# Colorize black & white photo
bash enhance-image.sh --op colorize --image-url "https://example.com/bw_photo.jpg"

# Anime style with prompt
bash enhance-image.sh \
  --op anime \
  --image-url "https://example.com/portrait.jpg" \
  --prompt "anime style portrait, detailed eyes"

# Product shot cleanup
bash enhance-image.sh --op product-shot --image-url "https://example.com/product.jpg"

# From local file
bash enhance-image.sh --op upscale --file "/path/to/image.jpg"
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--op` | Operation (see table above) | — |
| `--image-url` | Source image URL | — |
| `--file` | Local image file (auto-uploads) | — |
| `--face-url` | Face image URL (for face-swap) | — |
| `--mask-url` | Mask image URL (for object-erase) | — |
| `--prompt` | Optional text (for anime, product-photo) | — |
| `--async` | Return request_id immediately | — |
| `--timeout` | Max wait seconds | `300` |
| `--json` | Raw JSON output | — |

---

## Output Format

```json
{
  "request_id": "abc123",
  "status": "completed",
  "outputs": ["https://cdn.muapi.ai/files/abc123/edited.png"]
}
```

## Present Results to User

```
![Edited Image](https://cdn.muapi.ai/files/.../edited.png)
• Operation: Background Removed | Model: muapi.ai
```

## Troubleshooting

### Invalid Image URL
```
Error: Could not fetch image from URL
Make sure the URL is publicly accessible.
```

### Face Swap Failed
```
Error: No face detected in image
Ensure --face-url contains a clear, front-facing photo.
```
