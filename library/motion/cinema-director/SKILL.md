# 🎬 AI Cinema Director Skill

**A specialized skill for AI Agents to direct high-fidelity cinematic video.**
The Cinema Director skill translates high-level creative intent into technical cinematographic directives for state-of-the-art video models (Veo3, Kling, Luma).

## Core Competencies

1. **Shot Composition Analysis**: Mapping emotional beats to appropriate framing (e.g., Extreme Close-Up for intimacy, Wide Shot for isolation).
2. **Camera Movement Orchestration**: Directing complex physical movements (Dolly, Truck, Crane) and lens-based effects (Rack Focus, Dolly Zoom).
3. **Lighting & Atmosphere Design**: Specifying temporal and stylistic lighting (Golden Hour, Chiaroscuro, Volumetric God Rays).
4. **Technical Parameter Optimization**: Automatically selecting optimal frame rates, aspect ratios, and model-specific biases.

---

## 🏗️ Technical Specification

### 1. Intent Mapping Table

| Creative Intent | Framing | Movement | Lighting |
| :--- | :--- | :--- | :--- |
| **Heroic Reveal** | Low Angle / Wide | Crane Up / Orbit | Rim Lighting / High Contrast |
| **Tense/Uneasy** | Dutch Angle | Handheld Shake | Low Key / Harsh Shadows |
| **Introspective** | Close-Up | Slow Push In | Soft Rembrandt / Window Light |
| **Majestic/Epic** | Extreme Wide | Drone Flyover | Golden Hour / Volumetric |
| **Melancholic** | Profile / Medium | Slow Pull Out | Blue Hour / Desaturated |

### 2. Physical Camera Movements
- `Dolly In/Out`: Physical camera movement on a track toward/away from the subject.
- `Truck Left/Right`: Lateral physical movement.
- `Crane/Jib`: Sweeping vertical movement from a height.
- `Orbit`: Circular movement around a center point.
- `Pedestal`: Vertical elevation change (without tilting).

### 3. Lens & Optical Controls
- `Shallow DOF`: Background blur (Bokeh).
- `Anamorphic`: Horizontal flares and wide-screen cinematic feel.
- `Rack Focus`: Shifting focus between planes within the shot.

---

## 🚀 Protocol: Using the Cinema Director

### Step 1: Define the Creative Brief
Provide the agent with a subject and a "Director's Intent."

### Step 2: Invoke the Script
The `generate-film.sh` script accepts a `--brief` which it expands using its internal knowledge of cinematography.

```bash
# Directing a scene
bash scripts/generate-film.sh 
  --subject "A lone samurai in a blizzard" 
  --intent "epic reveal" 
  --model "kling-master"
```

### Step 3: Handle the Async Response
Video generation is asynchronous. Use the returned `request_id` to poll for completion via `core/platform/check-result.sh`.

---

## ⚠️ Constraints & Guardrails

- **Temporal Consistency**: Avoid complex subject transformations in a single shot (e.g., "man turns into a bird").
- **Movement Collisions**: Do not combine contradictory movements (e.g., "Dolly In" and "Dolly Out" simultaneously).
- **Physical Realism**: Prefer movements possible with real-world equipment for a more professional "film" look.
- **Model Bias**: 
  - `Veo3`: Best for slow, high-quality aesthetic shots.
  - `Kling`: Best for complex character motion and physics.
  - `Luma`: Best for fast-paced, high-action cinematic sequences.

---

## ⚙️ Implementation Details
This skill acts as an "Expert Translator" for the `core/media/generate-video.sh` primitive. It maintains a dictionary of cinematic styles and injects technical directives into the prompt before execution.
