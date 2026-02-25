#!/bin/bash
# Expert Skill: UI/UX Design Mockup Generator
# Translates product requirements into design system directives.

PLATFORM="mobile"
STYLE="modern"
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
    echo "Usage: bash generate-mockup.sh --desc 'travel app' [--platform mobile|web] [--style glassmorphism|brutalist] [--theme light|dark]"
    exit 1
fi

# Design System Logic
if [ "$PLATFORM" == "mobile" ]; then
    AR_FLAG="--aspect-ratio 9:16"
    LAYOUT="Card-based, bottom navigation bar, 8pt grid system"
    SYSTEM="iOS Human Interface Guidelines style"
else
    AR_FLAG="--aspect-ratio 16:9"
    LAYOUT="Sidebar navigation, grid layout, F-pattern hierarchy"
    SYSTEM="Modern SaaS Design System style"
fi

# Technical UX Brief
EXPERT_PROMPT="[UX_BRIEF]
PLATFORM: $PLATFORM
CONTEXT: $DESCRIPTION
DESIGN_SYSTEM: $SYSTEM, $THEME mode
STYLE_TOKENS: $STYLE aesthetic, geometric sans-serif (Inter), high-contrast accessibility
LAYOUT_PATTERN: $LAYOUT, professional whitespace, vector icons
[EXECUTE] Generate a high-fidelity flat UI mockup. NO hands, NO physical devices, NO background clutter. Pure digital interface only."

# Call Core Primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-image.sh"

bash "$CORE_SCRIPT" --prompt "$EXPERT_PROMPT" --model flux-dev $AR_FLAG --json
