#!/bin/bash
# Expert Skill: Nano-Banana (Gemini 3 Style)
# Implements the "Perfect Prompt" reasoning-driven formula.

SUBJECT=""
ACTION=""
CONTEXT=""
STYLE="cinematic"
LIGHTING="natural"
TEXT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --subject) SUBJECT="$2"; shift 2 ;;
        --action) ACTION="$2"; shift 2 ;;
        --context) CONTEXT="$2"; shift 2 ;;
        --style) STYLE="$2"; shift 2 ;;
        --lighting) LIGHTING="$2"; shift 2 ;;
        --text) TEXT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$SUBJECT" ]; then
    echo "Usage: bash generate-nano-art.sh --subject 'robot' [--action 'pouring coffee'] [--context 'cafe'] [--style 'photorealistic'] [--text 'CAFE']"
    exit 1
fi

# Text Logic
TEXT_PROMPT=""
if [ -n "$TEXT" ]; then
    TEXT_PROMPT="featuring a sign that clearly reads \"$TEXT\" in bold typography"
fi

# Nano-Banana Reasoning-Driven Prompt
# Formula: Subject + Action + Context + Lighting + Style + Text
EXPERT_PROMPT="[REASONING_BRIEF]
SUBJECT: A highly detailed $SUBJECT.
ACTION: The subject is $ACTION.
ENVIRONMENT: Situated in $CONTEXT.
LIGHTING: Illuminated by $LIGHTING lighting.
STYLE: Use a $STYLE aesthetic. High fidelity, physically accurate reflections and textures.
EXTRA: $TEXT_PROMPT.
[EXECUTE] Generate a photorealistic image based on this logic. Ensure precise composition and lighting interactions."

# Call Core Primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"

bash "$CORE_SCRIPT" --prompt "$EXPERT_PROMPT" --model "nano-banana" --json
