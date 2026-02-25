# 🖼️ Logo Creator Skill

**A specialized skill for AI Agents to engineer professional-grade brand identities.**
The Logo Creator skill translates brand vision into minimalist, scalable, and iconic vector-style marks.

## Core Competencies

1. **Geometric Primitive Construction**: Using basic shapes (circles, squares, triangles) to create memorable icons.
2. **Negative Space Manipulation**: Integrating secondary meanings through the strategic use of empty space.
3. **Symbolic Abstraction**: Reducing complex brand concepts into their simplest visual essence.
4. **Scalability Awareness**: Ensuring designs remain legible from 16px (favicon) to billboards.

---

## 🏗️ Technical Specification

### 1. Logo Taxonomy Table

| Logo Type | Style | Best For | Focus |
| :--- | :--- | :--- | :--- |
| **Pictorial Mark** | Symbolic | Established Brands | Iconic Recognition |
| **Abstract Mark** | Geometric | Tech/Modern | Concept/Feeling |
| **Lettermark/Monogram** | Typographic | Long Names | Initials/Font Style |
| **Emblem** | Traditional | Institutions/Cafe | Detail/Badge |
| **Mascot** | Character | Communities/Gaming | Personality |

### 2. Branding Directives
- `Color Theory`: Use high-impact monochromatic or dual-tone palettes for professional "weight."
- `Typography`: Pair marks with clean Geometric Sans-Serif (e.g., Gotham, Helvetica style).
- `Style`: Flat design ONLY. No gradients, 3D effects, or photorealistic textures.

---

## 🚀 Protocol: Using the Logo Creator

### Step 1: Define the Brand Brief
Provide the agent with a brand name, core values, and industry.

### Step 2: Invoke the Script
The `create-logo.sh` script generates a comprehensive branding brief.

```bash
# Designing a Fintech Logo
bash scripts/create-logo.sh \
  --brand "Aura" \
  --concept "geometric security shield with a spark" \
  --style minimalist \
  --color "black and gold"
```

---

## ⚠️ Constraints & Guardrails

- **Production-Ready**: **MANDATORY** - Solid white/black background only. No textures, no desks, no mockups (t-shirts/business cards).
- **Legibility**: Avoid thin lines or complex overlapping that disappears at small scales.
- **Minimalism**: Limit the mark to a maximum of 3 core geometric elements.
- **No Artifacting**: Use Flux for accurate brand name rendering within the logo.

---

## ⚙️ Implementation Details
This skill acts as a "Senior Brand Identity" layer over the `core/media/generate-image.sh` primitive. It generates an `EXPERT_LOGO_BRIEF` that enforces geometric symmetry and negative space principles.
