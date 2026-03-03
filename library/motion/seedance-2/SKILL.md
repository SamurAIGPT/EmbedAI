---
name: muapi-seedance-2
description: Expert Cinema Director skill for Seedance 2.0 (ByteDance) — high-fidelity video generation using technical camera grammar and multimodal references.
---

# 🎬 Seedance 2.0 Cinema Expert

**The definitive skill for "Director-Level" AI video orchestration.**
Seedance 2.0 is not a descriptive model; it is an *instructional* model. It responds best to technical cinematography, physics directives, and precise camera grammar.

## Core Competencies

1.  **Instructional Framework**: Moving away from "what it looks like" to "how to shoot it."
2.  **Multimodal Referencing**: Utilizing the `@tag` system (`@image1`, `@video1`, `@audio1`) for style, motion, and rhythm locking.
3.  **Temporal Consistency**: Maintaining character, clothing, and environment stability through instructional "persistence" prompts.
4.  **Audio-Visual Sync**: Native high-fidelity sound generation synchronized with visual motion.

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

## 🧠 Prompt Optimization Protocol (Agent Instruction)

**The Agent MUST transform user intent into a technical "Director Brief" before execution.**

1.  **Technical Grammar**: Use camera terms: *Dolly In/Out, Crane Shot, Whip Pan, Tracking Shot, Anamorphic Lens, Shallow Depth of Field*.
2.  **Physics Directives**: Describe light behavior: Use "caustic patterns," "volumetric rays," or "subsurface scattering" instead of "good lighting."
3.  **注文 references (Tags)**: If files are provided, refer to them explicitly: *"Replicate the camera movement trajectory of @video1 while maintaining the visual style of @image1."*
4.  **ORDER MATTERS**: Tokens at the start define composition; tokens at the end define texture and micro-motion.

---

## 🚀 Protocol: Using Seedance 2

### Step 1: Initialize the Brief
Identify the cinematography style (e.g., *Epic Reveal*, *Tense Close-up*, *Slow Narrative*).

### Step 2: Invoke the Expert Script
Use the specialized script to ensure all technical metadata is passed correctly.

```bash
# Example: Generating an 'Epic Reveal' for a concept
bash scripts/generate-seedance.sh \
  --subject "a hidden temple in the Andes" \
  --intent "epic" \
  --aspect "16:9" \
  --view
```

---

## ⚠️ Constraints & Guardrails

-   **No Keyword Soup**: DO NOT use "8k, masterpiece, trending." Use technical descriptions: "High-fidelity production grade, 24fps, cinematic grain."
-   **Continuous Action**: Describe *one fluid motion*. Avoid "The man runs and then he stops and then he eats." Use "The man gradually transitions from a sprint to a sudden stop, chest heaving."
-   **Face Stability**: If consistency is critical, include: *"Maintain high character consistency, zero facial flicker, persistent clothing details."*

---

## ⚙️ Implementation Details
This skill acts as a **Cinematographic Wrapper** for the `seedance-v2.0-t2v` model via the `muapi` core. It translates low-level creative intent into high-fidelity technical instructions.
