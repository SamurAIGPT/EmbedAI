# 🍌 Nano-Banana Expert Skill (Gemini 3 Style)

**A specialized skill for AI Agents to leverage "Reasoning-Driven" image generation.**
Based on the advanced prompting architecture of Google's Gemini 3 (Nano Banana Pro), this skill moves beyond keyword stuffing to structured, logic-based creative briefs.

## Core Competencies

1. **Reasoning-Driven Prompting**: Using natural language logic to define physics, lighting, and spatial relationships.
2. **Structured Creative Briefs**: Implementing the "Perfect Prompt" formula: `Subject + Action + Context + Composition + Lighting`.
3. **Text Rendering Precision**: Explicitly defining typography and signifiers for legible text integration.
4. **Contextual Grounding**: Using "Search Grounding" logic (simulated) to anchor generations in real-world accuracy.

---

## 🏗️ Technical Specification

### 1. The "Perfect Prompt" Formula

| Component | Description | Example |
| :--- | :--- | :--- |
| **Subject** | Detailed entity description | "A stoic robot barista with exposed copper wiring" |
| **Action** | Dynamic interaction | "Pouring a latte art leaf with mechanical precision" |
| **Context** | Environment & Atmosphere | "Inside a neon-lit cyberpunk cafe at midnight" |
| **Composition** | Camera & Lens choice | "Close-up, 85mm lens, f/1.8 aperture" |
| **Lighting** | Mood & Direction | "Volumetric blue rim light, warm cafe glow" |
| **Style** | Aesthetic anchor | "Cinematic, photorealistic, 4K production value" |

### 2. Advanced Features
- **Negative Constraint Logic**: Instead of "no blurry," use "Ensure sharp focus on the subject's eyes."
- **Identity Consistency**: (Simulated) "Maintain consistent facial structure across variations."
- **Text Integration**: Use double quotes for specific text: `The sign reads "OPEN 24/7"`.

---

## 🧠 Prompt Optimization Protocol (Agent Instruction)

**Before calling the script, the Agent MUST rewrite the user's prompt into a logic-driven Reasoning Brief:**

1. **NO KEYWORD SOUP**: Remove "8k, masterpiece, ultra-detailed." Use full, descriptive sentences.
2. **PHYSICAL CONSISTENCY**: Describe how elements interact (e.g., "The light from the crystal shards casts caustic patterns across the obsidian floor").
3. **TEXT PRECISION**: If the user wants text, define it precisely: `featuring a sign that says "STORE NAME" in a weathered serif font`.
4. **OPTICAL DIRECTIVES**: Specify lens behavior: *Shallow Depth of Field (f/1.8)*, *Macro Lens*, *Anamorphic Flare*.

---

## 🚀 Protocol: Using Nano-Banana

### Step 1: Define the Creative Logic
Provide the agent with a subject and a specific scenario.

### Step 2: Invoke the Script
The `generate-nano-art.sh` script translates the logic into a structured Gemini 3-style prompt.

```bash
# Generating a reasoning-driven image
bash scripts/generate-nano-art.sh \
  --subject "a glass chess piece" \
  --action "shattering into liquid shards" \
  --context "on a obsidian table" \
  --style "macro photography"
```

---

## ⚠️ Constraints & Guardrails

- **No Keyword Soup**: **MANDATORY** - Do not use "trending on artstation, masterpiece, 8k". Use natural language descriptions.
- **Physics Logic**: Ensure the prompt describes *physically possible* lighting and reflection interactions.
- **Full Sentences**: The model parses relationships; use "light reflecting off the water" instead of "water, reflection".

---

## ⚙️ Implementation Details
This skill applies a "Logic Wrapper" around the `core/media/generate-image.sh` primitive, converting fragmented inputs into a coherent, reasoning-ready narrative prompt.
