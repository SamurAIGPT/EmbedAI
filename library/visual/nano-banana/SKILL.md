# 🎭 Nano-Banana Expert Art Skill

**Expert high-fidelity image generation for AI Agents.**  
Uses the Google-pioneered "Nano-Banana" prompting framework (Pseudo-Code & Creative Briefs) to produce cinematic, professional-grade visual assets.

## 🚀 Usage

```bash
# Generate a cinematic shot
bash scripts/generate-nano-art.sh --subject "A futuristic robot gardener in a neon glasshouse" --style "photorealistic"
```

## ✨ Expert Features
- **Auto-Prompting** — Automatically wraps subjects in high-contrast lighting and cinematic composition variables.
- **Pseudo-Code Logic** — Uses structured prompting to prevent "prompt drift" in high-fidelity models.
- **Optimized for `flux-dev`** — Curated model selection for the best output-to-speed ratio.

## ⚙️ How it Works
This skill is a **domain-expert wrapper** around the `core/media/generate-image.sh` primitive. It handles the "Expert Knowledge" layer so the agent only needs to provide the **subject**.
