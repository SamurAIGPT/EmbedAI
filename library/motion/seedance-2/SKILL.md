---
name: muapi-seedance-2
version: 0.1.0
description: Expert Cinema Director skill for Seedance 2.0 (ByteDance) — high-fidelity video generation using technical camera grammar and multimodal references. Supports text-to-video, image-to-video, and video extension.
---

# 🎬 Seedance 2.0 Cinema Expert

**The definitive skill for "Director-Level" AI video orchestration.**
Seedance 2.0 is not a descriptive model; it is an *instructional* model. It responds best to technical cinematography, physics directives, and precise camera grammar.

## Core Competencies

1.  **Text-to-Video (t2v)**: Generate cinematic video from a Director Brief using `seedance-v2.0-t2v`.
2.  **Image-to-Video (i2v)**: Animate 1–9 reference images into a video using `seedance-v2.0-i2v`.
3.  **Video Extension (extend)**: Seamlessly continue an existing Seedance 2.0 video using `seedance-v2.0-extend`.
4.  **Multimodal Referencing**: Utilize `@tag` system (`@image1`, `@video1`) for style, motion, and rhythm locking.
5.  **Audio-Visual Sync**: Native high-fidelity sound generation synchronized with visual motion.
6.  **Temporal Consistency**: Maintain character, clothing, and environment stability across shots.

---

## 🏗️ Technical Specification: The Director Brief

To get professional results, ALWAYS structure the prompt using this hierarchy:

| Component | Instruction Type | Example |
| :--- | :--- | :--- |
| **Scene** | Environment + Lighting | "A rain-soaked cyberpunk street, magenta neon reflections on wet asphalt." |
| **Subject** | Identity + Detail | "A woman in a black trenchcoat, determined focus, cinematic skin textures." |
| **Action** | Fluid Interaction | "Walking forward through the crowd, coat billowing slightly in the wind." |
| **Camera** | Movement + Lens | "Medium tracking shot, 35mm lens, slow dolly backward. Subtle handheld jitter." |
| **Style** | Mood + Intent | "Cinematic epic, warm color grade, shallow DOF, rack focus to subject's face." |

---

## 🧠 Prompt Optimization Protocol

**The Agent MUST transform user intent into a technical "Director Brief" before execution.**

1.  **Technical Grammar**: Use camera terms: *Dolly In/Out, Crane Shot, Whip Pan, Tracking Shot, Anamorphic Lens, Shallow Depth of Field*.
2.  **Physics Directives**: Use "caustic patterns," "volumetric rays," or "subsurface scattering" instead of "good lighting."
3.  **Timecode Notation**: For multi-beat scenes, use `[00:00-00:05s]` format to specify timing.
4.  **Tag References**: If files provided, use: *"Replicate the camera movement of @video1 while maintaining the visual style of @image1."*
5.  **ORDER MATTERS**: Tokens at the start define composition; tokens at the end define texture and micro-motion.
6.  **Multi-Image i2v**: Provide up to 9 reference images. The model blends aspects (style, identity, environment) across all inputs.

---

## 🚀 Protocol: Using Seedance 2

### Mode 1: Text-to-Video (t2v)

```bash
# Epic reveal shot
bash scripts/generate-seedance.sh \
  --subject "a hidden temple in the Andes, mist rolling through the canopy" \
  --intent "epic" \
  --aspect "16:9" \
  --duration 10 \
  --quality high \
  --view

# Tense close-up, vertical for social
bash scripts/generate-seedance.sh \
  --subject "a detective examines a cryptic clue under harsh lamp light" \
  --intent "tense" \
  --aspect "9:16" \
  --duration 5
```

### Mode 2: Image-to-Video (i2v)

Animate one or more reference images. Up to 9 images can be supplied — the model synthesizes motion across all of them.

```bash
# Animate a single local image
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file hero.jpg \
  --subject "hero strides forward, coat billowing in slow motion" \
  --intent "epic" \
  --aspect "16:9" \
  --view

# Animate from a URL
bash scripts/generate-seedance.sh \
  --mode i2v \
  --image "https://example.com/scene.jpg" \
  --subject "camera slowly pulls back to reveal the full landscape" \
  --intent "reveal" \
  --duration 10

# Multi-image blending (character + environment + style reference)
bash scripts/generate-seedance.sh \
  --mode i2v \
  --file character.jpg \
  --file environment.jpg \
  --image "https://example.com/style.jpg" \
  --subject "character walks through the environment in cinematic style" \
  --quality high
```

### Mode 3: Extend Video

Continue an existing Seedance 2.0 video seamlessly, preserving visual style, motion, and audio.

```bash
# Extend with no new prompt (model continues naturally)
bash scripts/generate-seedance.sh \
  --mode extend \
  --request-id "abc-123-def-456" \
  --duration 10

# Extend with directional prompt
bash scripts/generate-seedance.sh \
  --mode extend \
  --request-id "abc-123-def-456" \
  --subject "camera continues to pull back, revealing the vast city below" \
  --intent "reveal" \
  --duration 10 \
  --quality high \
  --view
```

### Async Pattern (for long jobs)

```bash
# Submit and get request_id immediately
RESULT=$(bash scripts/generate-seedance.sh --mode i2v --file photo.jpg --async --json)
REQUEST_ID=$(echo "$RESULT" | jq -r '.request_id')

# Check later
bash ../../../../core/media/generate-video.sh --result "$REQUEST_ID"
```

---

## ⚠️ Constraints & Guardrails

-   **No Keyword Soup**: DO NOT use "8k, masterpiece, trending." Use technical descriptions: "High-fidelity production grade, 24fps, cinematic grain."
-   **Continuous Action**: Describe *one fluid motion*. Avoid "The man runs and then he stops." Use "The man gradually transitions from a sprint to a sudden stop, chest heaving."
-   **Face Stability**: For consistent characters: *"Maintain high character consistency, zero facial flicker, persistent clothing details."*
-   **Extension Only Works on v2.0**: `--mode extend` requires a `request_id` from a previous `seedance-v2.0-t2v` or `seedance-v2.0-i2v` job.
-   **Aspect Ratios**: 16:9, 9:16, 4:3, 3:4 (Seedance 2.0 supports all four).
-   **Duration**: 5, 10, or 15 seconds.
-   **Quality**: `basic` (faster) or `high` (higher fidelity).

---

## 🎭 Prompt Templates (from awesome-seedance community)

### Cinematic Film Styles
```
[SCENE] Rain-soaked cyberpunk alley, neon signs reflected on wet cobblestones.
[SUBJECT] A lone figure in a weathered trench coat, face obscured by a wide-brim hat.
[ACTION] Walking slowly, each step splashing neon color into the puddles.
[CAMERA] Low-angle tracking shot, anamorphic lens, slow dolly in. Rack focus to face.
[STYLE] Denis Villeneuve aesthetic, high contrast, desaturated blues and magentas. 24fps.
```

### Advertising / Product Motion
```
[SCENE] Minimalist white studio, single product on a rotating pedestal.
[ACTION] Subtle 360° rotation, product details catching specular highlights.
[CAMERA] Tight medium shot, macro lens pass over surface texture, slow orbit.
[STYLE] Commercial grade, perfect exposure, zero background distraction.
```

### Action / Physics
```
[SCENE] Desert canyon at sunrise, sandy terrain, long shadows.
[SUBJECT] High-performance sports car accelerating through a turn.
[ACTION] Rear wheels spinning with dust plume, chassis flexing under g-force.
[CAMERA] Low hero angle dolly tracking alongside, then whip pan to lead car.
[STYLE] Hollywood racing film, warm golden grade, motion blur on wheels. 24fps.
```

### Character Consistency (Martial Arts / Action)
```
[SUBJECT] Same fighter throughout: young woman, white gi, black belt, determined expression.
[ACTION] Fluid kata sequence — rising block, stepping side kick, spinning back fist.
[CAMERA] Full-body wide shot, then cut to close-up of fist impact in slow motion.
[STYLE] Maintain identical lighting, clothing, and facial features in every frame. Zero flicker.
```

---

## ⚙️ Implementation Details

| Model | Endpoint | Use Case |
|:---|:---|:---|
| `seedance-v2.0-t2v` | Text-to-Video | Generate from Director Brief |
| `seedance-v2.0-i2v` | Image-to-Video | Animate 1–9 reference images |
| `seedance-v2.0-extend` | Extend Video | Continue a v2.0 generated video |

This skill acts as a **Cinematographic Wrapper** that translates low-level creative intent into high-fidelity technical instructions for the `muapi` core.
