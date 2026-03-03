#!/bin/bash
# Expert Skill: Seedance 2 Cinema Expert
# Translates creative intent into 'Director-Level' technical directives for Seedance 2.0.

SUBJECT=""
INTENT="cinematic"
ASPECT="16:9"
DURATION=5
QUALITY="basic"
AUDIO_FLAG=""
VIEW_FLAG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --subject) SUBJECT="$2"; shift 2 ;;
        --intent) INTENT="$2"; shift 2 ;;
        --aspect) ASPECT="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --quality) QUALITY="$2"; shift 2 ;;
        --no-audio) AUDIO_FLAG="--no-audio"; shift ;;
        --view) VIEW_FLAG="--view"; shift ;;
        --help|-h)
            echo "Seedance 2 Cinema Expert"
            echo "Usage: bash generate-seedance.sh --subject 'description' [options]"
            echo "Options:"
            echo "  --intent reveal|tense|epic|narrative (default: cinematic)"
            echo "  --aspect 16:9|9:16|1:1"
            echo "  --duration 5|10|15"
            echo "  --quality basic|high"
            echo "  --no-audio"
            echo "  --view"
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$SUBJECT" ]; then
    echo "Error: --subject is required."
    exit 1
fi

# Director's Logistics & Grammar
case $INTENT in
    "reveal")
        MOVEMENT="Slow crane up and tilt down, wide shot."
        LIGHTING="Volumetric god rays, golden hour atmosphere."
        OPTICS="Deep focus, high clarity lens."
        ;;
    "tense")
        MOVEMENT="Handheld jittery movement, dutch angle close-up."
        LIGHTING="Low key, harsh shadows, flickering magenta neon."
        OPTICS="Shallow depth of field, anamorphic flare."
        ;;
    "epic")
        MOVEMENT="Dolly in with circular orbit, low angle."
        LIGHTING="Dramatic rim lighting, high contrast cinematic grade."
        OPTICS="Anamorphic 35mm, sharp focus on subject."
        ;;
    "narrative")
        MOVEMENT="Smooth tracking shot, following subject walking."
        LIGHTING="Natural soft light, blue hour tones."
        OPTICS="Standard 50mm, realistic bokeh."
        ;;
    *)
        MOVEMENT="Smooth cinematic pan, balanced framing."
        LIGHTING="Natural studio lighting, balanced highlights."
        OPTICS="Standard cinematic lens."
        ;;
esac

# Construct the technical Director Brief
DIRECTOR_PROMPT="[SCENE] $SUBJECT. [LIGHTING] $LIGHTING [ACTION] Fluid continuous motion. [CAMERA] $MOVEMENT [STYLE] $OPTICS High-fidelity production grade, 24fps. Maintain high character consistency, zero flicker."

# Call the Core primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-video.sh"

if [ ! -f "$CORE_SCRIPT" ]; then
    echo "Error: Core script not found at $CORE_SCRIPT"
    exit 1
fi

bash "$CORE_SCRIPT" \
  --prompt "$DIRECTOR_PROMPT" \
  --model "seedance-v2.0-t2v" \
  --aspect-ratio "$ASPECT" \
  --duration "$DURATION" \
  $AUDIO_FLAG $VIEW_FLAG --async --json
