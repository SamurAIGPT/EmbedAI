---
name: muapi-video-edit
description: Edit videos using muapi.ai AI effects. Use when user requests "Add lipsync", "Apply video effects", "Dance effects", "Face swap in video", "Change outfit in video", "Modify video with AI", "Add sound effects to video", or similar video editing tasks.
metadata:
  author: muapi-ai
  version: "1.0.0"
---

# muapi.ai Video Edit

AI-powered video effects, lipsync, transformations, and editing.

## Scripts

| Script | Purpose |
|--------|---------|
| `video-effects.sh` | Video/image effects, dance, face swap, dress change, Wan AI effects, Luma |
| `lipsync.sh` | Lipsync: sync audio to video using Sync, LatentSync, Creatify, or Veed |

---

## Video Effects

```bash
bash /mnt/skills/user/muapi-video-edit/scripts/video-effects.sh [options]
```

### Operations (`--op`)

| Flag | Endpoint | Description | Inputs |
|------|----------|-------------|--------|
| `wan-effects` | `generate_wan_ai_effects` | Wan AI creative effects | `--image-url`, `--prompt` |
| `video-effect` | `video-effects` | Apply named effect to video | `--video-url`, `--effect` |
| `image-effect` | `image-effects` | Apply named effect to image | `--image-url`, `--effect` |
| `dance` | `ai-dance-effects` | Apply dance animation | `--image-url`, `--audio-url` |
| `face-swap` | `ai-video-face-swap` | Swap face in video | `--video-url`, `--face-url` |
| `dress-change` | `ai-dress-change` | Change outfit | `--image-url`, `--prompt` |
| `luma-modify` | `luma-modify-video` | Modify video with text prompt | `--video-url`, `--prompt` |
| `luma-reframe` | `luma-flash-reframe` | Reframe/crop video | `--video-url`, `--prompt` |
| `vidu-reference` | `vidu-q1-reference` | Vidu character reference | `--image-url`, `--prompt` |

### Examples

```bash
# Wan AI effects (image → animated)
bash video-effects.sh \
  --op wan-effects \
  --image-url "https://example.com/photo.jpg" \
  --prompt "make the character dance in the rain"

# Apply a named video effect
bash video-effects.sh \
  --op video-effect \
  --video-url "https://example.com/video.mp4" \
  --effect "slow-motion"

# Dance effects (animate person to music)
bash video-effects.sh \
  --op dance \
  --image-url "https://example.com/person.jpg" \
  --audio-url "https://example.com/music.mp3"

# Face swap in video
bash video-effects.sh \
  --op face-swap \
  --video-url "https://example.com/video.mp4" \
  --face-url "https://example.com/face.jpg"

# Change outfit
bash video-effects.sh \
  --op dress-change \
  --image-url "https://example.com/person.jpg" \
  --prompt "wearing a formal black suit"

# Luma video modification
bash video-effects.sh \
  --op luma-modify \
  --video-url "https://example.com/video.mp4" \
  --prompt "add falling snow to the background"
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--op` | Operation (see table) | — |
| `--video-url` | Source video URL | — |
| `--image-url` | Source image URL | — |
| `--face-url` | Face image URL (for face-swap) | — |
| `--audio-url` | Audio URL (for dance effects) | — |
| `--prompt` | Text description / instruction | — |
| `--effect` | Named effect name | — |
| `--async` | Return request_id immediately | — |
| `--timeout` | Max wait seconds | `300` |
| `--json` | Raw JSON output | — |

---

## Lipsync

```bash
bash /mnt/skills/user/muapi-video-edit/scripts/lipsync.sh [options]
```

Sync audio to video — makes the person in the video appear to speak the audio.

### Models (`--model`)

| Flag | Endpoint | Description |
|------|----------|-------------|
| `sync` | `sync-lipsync` | Sync Labs — high quality **(default)** |
| `latent` | `latentsync-video` | LatentSync — open source |
| `creatify` | `creatify-lipsync` | Creatify — fast |
| `veed` | `veed-lipsync` | Veed — reliable |

### Examples

```bash
# Basic lipsync
bash lipsync.sh \
  --video-url "https://example.com/person.mp4" \
  --audio-url "https://example.com/speech.mp3"

# Using LatentSync (open source)
bash lipsync.sh \
  --video-url "https://example.com/person.mp4" \
  --audio-url "https://cdn.muapi.ai/files/abc/speech.mp3" \
  --model latent

# Async (lipsync takes 30–120s)
bash lipsync.sh \
  --video-url "https://example.com/person.mp4" \
  --audio-url "https://example.com/speech.mp3" \
  --async
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--video-url` | Source video URL (required) | — |
| `--audio-url` | Source audio URL (required) | — |
| `--model` | Lipsync model (see table) | `sync` |
| `--async` | Return request_id immediately | — |
| `--timeout` | Max wait seconds | `300` |
| `--json` | Raw JSON output | — |

---

## Output Format

```json
{
  "request_id": "abc123",
  "status": "completed",
  "outputs": ["https://cdn.muapi.ai/files/abc123/result.mp4"]
}
```

## Present Results to User

```
[View Edited Video](https://cdn.muapi.ai/files/.../result.mp4)
• Operation: Lipsync | Model: Sync Labs
```

## Tips

- **Lipsync**: Works best with a clear, front-facing video of a single person speaking
- **Dance effects**: Use `--audio-url` with an upbeat track for best results
- **Face swap**: `--face-url` should be a clear, front-facing photo
- Use `--async` for video operations — they typically take 30–120 seconds
