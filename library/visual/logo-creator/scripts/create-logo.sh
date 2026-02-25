#!/bin/bash
# Expert Skill: Logo Creator
# Generates minimalist, vector-style logos suitable for branding.

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
    echo "Usage: bash create-logo.sh --concept 'fox head' --brand 'Foxy' [--style minimalist|flat|mascot] [--color 'orange on white']"
    exit 1
fi

# Expert Logo Prompt
EXPERT_PROMPT="[LOGO_BRIEF]
BRAND: \"$BRAND\"
ICON: $CONCEPT
STYLE: $STYLE vector logo, simple geometric shapes, flat design
COLOR: $COLOR background
[EXECUTE] Generate a clean, professional vector logo. CENTERED. NO realistic shading, NO complex details, NO 3D rendering. Just the mark and text."

# Call Core Primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"

bash "$CORE_SCRIPT" --prompt "$EXPERT_PROMPT" --model flux-dev --aspect-ratio 1:1 --json
