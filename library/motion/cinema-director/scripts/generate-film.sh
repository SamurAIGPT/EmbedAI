#!/bin/bash
# Expert Skill: AI Cinema Director
# Translates high-level intent into technical cinematographic directives.

SUBJECT=""
INTENT="cinematic"
MODEL="veo3"
ASPECT="16:9"
DURATION=5
AUDIO_FLAG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --subject) SUBJECT="$2"; shift 2 ;;
        --intent) INTENT="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --aspect) ASPECT="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --no-audio) AUDIO_FLAG="--no-audio"; shift ;;
        *) shift ;;
    esac
done

if [ -z "$SUBJECT" ]; then
    echo "Usage: bash generate-film.sh --subject 'description' [--intent reveal|tense|epic] [--model veo3|kling] [--duration 5|10] [--no-audio]"
    exit 1
fi

# Director's Logic Table (Internal Knowledge)
case $INTENT in
    "reveal")
        FRAMING="Extreme wide shot"
        MOVEMENT="Slow crane up and tilt down"
        LIGHTING="Golden hour, volumetric god rays"
        LENS="Deep focus, high clarity"
        ;;
    "tense")
        FRAMING="Dutch angle, close-up"
        MOVEMENT="Handheld jittery movement"
        LIGHTING="Low key, harsh shadows, flickering neon"
        LENS="Shallow depth of field, anamorphic lens flare"
        ;;
    "epic")
        FRAMING="Low angle wide shot"
        MOVEMENT="Dolly in with circular orbit"
        LIGHTING="Dramatic rim lighting, high contrast"
        LENS="Anamorphic, 35mm film grain"
        ;;
    "melancholy")
        FRAMING="Medium shot, profile"
        MOVEMENT="Slow dolly out"
        LIGHTING="Blue hour, soft desaturated tones"
        LENS="Shallow bokeh, soft focus"
        ;;
    *)
        FRAMING="Cinematic medium shot"
        MOVEMENT="Smooth pan"
        LIGHTING="Natural studio lighting"
        LENS="Standard 50mm"
        ;;
esac

# Construct the technical Director's Prompt
DIRECTOR_PROMPT="[DIRECTOR_BRIEF]
SCENE: $SUBJECT
FRAMING: $FRAMING
CAMERA_MOTION: $MOVEMENT
LIGHTING_DESIGN: $LIGHTING
OPTICS: $LENS
[EXECUTE] High-fidelity cinematic footage. Professional color grade, 4k, hyper-realistic physics."

# Call the Core primitive
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-video.sh"

bash "$CORE_SCRIPT" --prompt "$DIRECTOR_PROMPT" --model "$MODEL" --aspect-ratio "$ASPECT" --duration "$DURATION" $AUDIO_FLAG --async --json
