#!/bin/bash
# Expert Skill: Nano-Banana Art Generator
# Implements Google's "Nano-Banana" prompting framework for high-fidelity images.

SUBJECT=""
STYLE="photorealistic"

while [[ $# -gt 0 ]]; do
    case $1 in
        --subject) SUBJECT="$2"; shift 2 ;;
        --style) STYLE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$SUBJECT" ]; then
    echo "Usage: bash generate-nano-art.sh --subject 'description' [--style photorealistic|3d|illustration]"
    exit 1
fi

# Apply "Nano-Banana" Pseudo-Code Prompt Engineering
EXPERT_PROMPT="[VARIABLES]
SUBJECT = \"$SUBJECT\"
STYLE = \"$STYLE\"
COMPOSITION = \"Cinematic wide shot, f/1.8 aperture, shallow depth of field\"
LIGHTING = \"Volumetric lighting, high-contrast rim light, golden hour glow\"
[EXECUTE] Generate a high-fidelity image of SUBJECT in STYLE using COMPOSITION and LIGHTING. 8k resolution, raw texture, hyper-detailed."

# Call the Core primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"
bash "$CORE_SCRIPT" --prompt "$EXPERT_PROMPT" --model flux-dev --json
