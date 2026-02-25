#!/bin/bash
# Expert Skill: UI/UX Design Mockup Generator
# Generates high-fidelity UI designs for web and mobile using Flux/Midjourney best practices.

PLATFORM="mobile"
STYLE="modern clean"
THEME="light"
DESCRIPTION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --platform) PLATFORM="$2"; shift 2 ;;
        --style) STYLE="$2"; shift 2 ;;
        --theme) THEME="$2"; shift 2 ;;
        --desc) DESCRIPTION="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$DESCRIPTION" ]; then
    echo "Usage: bash generate-mockup.sh --desc 'travel app home screen' [--platform mobile|web] [--style modern|glassmorphism] [--theme light|dark]"
    exit 1
fi

# Aspect Ratio Logic
if [ "$PLATFORM" == "mobile" ]; then
    AR_FLAG="--aspect-ratio 9:16"
    PLATFORM_KW="High-fidelity iPhone 15 Pro UI mockup"
else
    AR_FLAG="--aspect-ratio 16:9"
    PLATFORM_KW="High-fidelity Desktop Website UI mockup"
fi

# Expert Prompt Construction
EXPERT_PROMPT="[UX_BRIEF]
TYPE: $PLATFORM_KW
CONTEXT: $DESCRIPTION
STYLE: $STYLE, $THEME mode, vector icons, inter font
LAYOUT: Professional dribbble style, clean whitespace, user-centric
[EXECUTE] Generate a flat, high-quality UI design. No hand holding device, just the interface."

# Call Core Primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"

bash "$CORE_SCRIPT" --prompt "$EXPERT_PROMPT" --model flux-dev $AR_FLAG --json
