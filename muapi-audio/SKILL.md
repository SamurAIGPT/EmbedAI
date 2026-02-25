---
name: muapi-audio
description: Generate music and audio using muapi.ai. Use when user requests "Create music", "Generate a song", "Make background music", "Add audio to video", "Text to audio", "Remix this song", "Extend this track", or similar audio/music generation tasks.
metadata:
  author: muapi-ai
  version: "1.0.0"
---

# muapi.ai Audio

Generate music and audio effects using Suno and MMAudio models.

## Scripts

| Script | Purpose |
|--------|---------|
| `create-music.sh` | Suno music generation (create/remix/extend) and MMAudio (text-to-audio, video-to-audio) |

---

## Create Music

```bash
bash /mnt/skills/user/muapi-audio/scripts/create-music.sh [options]
```

### Operations (`--op`)

| Flag | Endpoint | Description |
|------|----------|-------------|
| `create` | `suno-create-music` | Generate new music from style + prompt **(default)** |
| `remix` | `suno-remix-music` | Remix an existing track |
| `extend` | `suno-extend-music` | Extend an existing track |
| `text-to-audio` | `mmaudio-v2/text-to-audio` | Generate audio/SFX from text description |
| `video-to-audio` | `mmaudio-v2/video-to-video` | Add/replace audio for a video |

---

### Suno Music Creation

```bash
# Create music with style and prompt
bash create-music.sh \
  --op create \
  --style "lo-fi hip hop, chill, relaxing" \
  --prompt "beats for studying late at night"

# Specify model version
bash create-music.sh \
  --op create \
  --style "pop, upbeat, female vocals" \
  --prompt "summer road trip anthem" \
  --suno-model V5

# Async (music takes 30–90s)
bash create-music.sh \
  --op create \
  --style "epic orchestral, cinematic" \
  --prompt "battle scene, rise of heroes" \
  --async
```

**Suno Models (`--suno-model`):** `V3_5`, `V4`, `V4_5`, `V4_5PLUS`, `V5` (default)

### Suno Remix

```bash
bash create-music.sh \
  --op remix \
  --audio-url "https://cdn.muapi.ai/files/abc123/track.mp3" \
  --style "jazz remix" \
  --prompt "smooth jazz version"
```

### Suno Extend

```bash
bash create-music.sh \
  --op extend \
  --audio-url "https://cdn.muapi.ai/files/abc123/track.mp3" \
  --prompt "continue the melody"
```

---

### MMAudio Text-to-Audio

Generate audio effects or ambient sound from text.

```bash
# Generate sound effect
bash create-music.sh \
  --op text-to-audio \
  --prompt "thunderstorm with heavy rain" \
  --duration 10

# Background ambience
bash create-music.sh \
  --op text-to-audio \
  --prompt "busy coffee shop ambience, soft chatter" \
  --duration 30
```

### MMAudio Video-to-Audio

Add or replace audio for a video, matching the visual content.

```bash
bash create-music.sh \
  --op video-to-audio \
  --video-url "https://cdn.muapi.ai/files/abc123/video.mp4" \
  --prompt "dramatic orchestral music matching the action"
```

---

## Arguments Reference

| Argument | Description | Default |
|----------|-------------|---------|
| `--op` | Operation (see table) | `create` |
| `--style` | Music style description (Suno) | — |
| `--prompt` | Music/audio description | — |
| `--suno-model` | Suno version: V3_5, V4, V4_5, V4_5PLUS, V5 | `V5` |
| `--audio-url` | Source audio URL (remix/extend) | — |
| `--video-url` | Source video URL (video-to-audio) | — |
| `--duration` | Audio duration in seconds (MMAudio) | `10` |
| `--async` | Return request_id immediately | — |
| `--timeout` | Max wait seconds | `300` |
| `--json` | Raw JSON output | — |

---

## Output Format

```json
{
  "request_id": "abc123",
  "status": "completed",
  "outputs": ["https://cdn.muapi.ai/files/abc123/music.mp3"]
}
```

## Present Results to User

```
Here's your generated music:

[Download track](https://cdn.muapi.ai/files/.../music.mp3)
• Style: lo-fi hip hop | Model: Suno V5
```

## Tips

- **Suno style**: Be specific — e.g., `"lo-fi hip hop, 90 bpm, vinyl crackle, piano melody"` produces better results than just `"lo-fi"`
- **MMAudio video-to-audio**: Works best when your prompt describes sounds that match the visual content
- Use `--async` for all operations — music generation typically takes 30–90 seconds
