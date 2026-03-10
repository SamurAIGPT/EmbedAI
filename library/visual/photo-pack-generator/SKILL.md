---
name: muapi-photo-pack-generator
description: Generate a pack of professional or aesthetic photos from a single reference image while preserving the exact identity of the person.
---

# 📸 Photo Pack Generator Expert Skill (Identity-Lock Edition)

Transform a single reference photo into a collection of themed images while maintaining **extremely high facial identity fidelity**.

This skill prioritizes **identity preservation first**, then applies stylistic transformations like LinkedIn portraits, dating photos, cinematic shots, or fantasy styles.

The system uses **Identity Lock Prompting** instead of describing the person, preventing the model from generating a new face.

---

# Core Principles

## 1️⃣ Identity Lock (MOST IMPORTANT)

The generated images must always depict **the same person from the reference image**.

All prompts MUST include identity lock instructions.

Required identity rules:

- Preserve the exact facial identity from the reference image
- Do not modify eye shape or spacing
- Do not modify nose structure
- Do not modify jawline or chin shape
- Do not modify cheekbones
- Do not modify face proportions
- Identity must remain identical to the reference photo

---

## 2️⃣ Vision-First Scene Analysis

The agent MUST analyze the reference image before generation.

However the analysis **must NOT describe the person** (age, ethnicity, hair etc).

Allowed analysis fields:

- head orientation
- facial angle
- expression
- lighting direction
- framing (portrait / half body / full body)

Example:

Head orientation: slight left tilt  
Expression: neutral friendly  
Lighting: soft frontal light  
Framing: head and shoulders portrait

---

# Agent Execution Flow

## Step 1 — Grounding Check

Ensure the user has provided a reference image.

Supported inputs:

- local image
- URL
- uploaded file

---

## Step 2 — Vision Analysis

Extract scene attributes only.

DO NOT describe:

- age
- ethnicity
- beard
- hair
- body type

Identity must come directly from the image.

---

## Step 3 — Category Selection

If the user does not specify a category suggest:

- LinkedIn
- Tinder
- OldMoney

---

## Step 4 — Prompt Construction

Use the reference image as the identity source.

Preserve the exact facial identity from the reference image.

Identity must remain identical to the reference photo.

Do not change:

- eye shape
- eye spacing
- nose structure
- jawline
- cheekbones
- face proportions

Maintain similar head orientation as the reference.

Scene example:

Outdoor café portrait  
Soft natural daylight  
35mm portrait lens  
Shallow depth of field  
Photorealistic skin texture

---

## Step 5 — Negative Prompt

Always include:

different person  
altered face  
changed facial features  
new identity  
generic face  
beautified face  
plastic skin  
face distortion  

---

## Step 6 — Execution

Example:

bash scripts/generate-pack.sh \
  --image "./my_face.jpg" \
  --category "LinkedIn" \
  --identity-lock true \
  --num 5

---

# Supported Categories

| Category | Best For | Aesthetic |
|---|---|---|
| LinkedIn | Professional | Studio |
| CEO | Founders | Office |
| Tinder | Dating | Lifestyle |
| OldMoney | Luxury | Estate |
| Cyberpunk | Fantasy | Neon |
| Fitness | Gym | Athletic |
| Travel | Social | Bali/Paris |
| 90s | Retro | Vintage |
| Holiday | Seasonal | Festive |

---

# Guardrails

## Fidelity First

Identity preservation is always more important than style.

## Never Re-Describe the Person

Avoid prompts like:

"Indian man in his 20s with short hair"

This causes the model to generate a **new face**.

Identity must come from the **reference image only**.

---

# Recommended Models

Best results with:

- nano-banana-edit

---

# Result

This system produces:

- consistent identity
- photorealistic images
- multi-style photo packs
- professional outputs
