#!/bin/bash
# Expert Skill: Logo Creator
# Translates brand vision into professional vector-style branding.

BRAND=""
STYLE="minimalist"
COLOR="black on white"
CONCEPT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --brand) BRAND="$2"; shift 2 ;;
        --style) STYLE="$2"; shift 2 ;;
        --color) COLOR="$2"; shift 2 ;;
        --concept) CONCEPT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$CONCEPT" ]; then
    echo "Usage: bash create-logo.sh --concept 'description' --brand 'name' [--style minimalist|abstract|mascot] [--color 'palette']"
    exit 1
fi

# Branding Logic
EXPERT_PROMPT="[LOGO_BRIEF]
BRAND_NAME: \"$BRAND\"
ICON_CONCEPT: $CONCEPT
DESIGN_PHILOSOPHY: $STYLE, geometric primitives, negative space, flat design
TECHNICAL: High scalability, solid $COLOR background, symmetric, centered
TYPOGRAPHY: Geometric sans-serif wordmark
[EXECUTE] Generate a clean, professional vector logo. NO 3D rendering, NO realistic shading, NO complex gradients. Pure brand identity mark."

# Call Core Primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"

bash "$CORE_SCRIPT" --prompt "$EXPERT_PROMPT" --model flux-dev --aspect-ratio 1:1 --json
