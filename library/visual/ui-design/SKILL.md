# 🎨 UI/UX Design Mockup Skill

**Create high-fidelity app and website mockups instantly.**
Generates clean, modern UI designs optimized for developer handoff and mood boarding.

## 🚀 Usage

```bash
# Generate a mobile app screen
bash scripts/generate-mockup.sh --desc "crypto wallet home screen" --platform mobile --style "dark mode cyberpunk"

# Generate a SaaS dashboard
bash scripts/generate-mockup.sh --desc "analytics dashboard for ecommerce" --platform web --style "clean minimal"
```

## ✨ Expert Features
- **Platform-Aware**: Automatically sets correct aspect ratios (9:16 for mobile, 16:9 for web).
- **Style Injection**: Supports "Glassmorphism", "Neomorphism", and "Flat Design" keywords.
- **Developer-Ready**: Generates flat layouts without distracting "hand-holding-phone" stock photo elements.

## ⚙️ How it Works
Wraps the `core/media/generate-image.sh` primitive with an expert UI design brief.
