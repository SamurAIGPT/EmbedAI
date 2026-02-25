# 🖼️ Logo Creator Skill

**Design minimalist, vector-ready logos in seconds.**
Generates clean, scalable brand marks suitable for startups, apps, and products.

## 🚀 Usage

```bash
# Generate a simple tech startup logo
bash scripts/create-logo.sh --brand "CloudSync" --concept "cloud with sync arrows" --style minimalist --color "blue on white"

# Create a mascot logo
bash scripts/create-logo.sh --brand "PizzaFox" --concept "fox eating pizza" --style mascot
```

## ✨ Expert Features
- **Negative Prompting**: Suppresses 3D rendering, shading, and realistic textures for true vector feel.
- **Brand Consistency**: Uses specific keywords (`flat`, `geometric`, `centered`) for professional results.
- **Text Handling**: Leverages Flux models for surprisingly accurate brand name rendering.

## ⚙️ How it Works
Wraps the `core/media/generate-image.sh` primitive with an expert branding brief.
