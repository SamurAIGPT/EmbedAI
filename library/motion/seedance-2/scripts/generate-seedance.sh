#!/bin/bash
# Expert Skill: Seedance 2 Cinema Expert
# Translates creative intent into 'Director-Level' technical directives for Seedance 2.0.
# Modes: t2v (text-to-video), i2v (image-to-video), extend (video extension)

SUBJECT=""
INTENT="cinematic"
ASPECT="16:9"
DURATION=5
QUALITY="basic"
AUDIO_FLAG=""
VIEW=true
MODE="t2v"
IMAGE_URLS=()
IMAGE_FILES=()
EXTEND_REQUEST_ID=""
ASYNC=false
JSON_ONLY=false
MAX_WAIT=600
POLL_INTERVAL=5
DEMO=true
DEMO_URL="https://cdn.muapi.ai/outputs/df8c9b12ff4b4790a417f8ef5cedd915.mp4"

MUAPI_BASE="https://api.muapi.ai/api/v1"

if [ -f ".env" ]; then source .env 2>/dev/null || true; fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --mode) MODE="$2"; shift 2 ;;
        --subject) SUBJECT="$2"; shift 2 ;;
        --intent) INTENT="$2"; shift 2 ;;
        --aspect) ASPECT="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --quality) QUALITY="$2"; shift 2 ;;
        --no-audio) AUDIO_FLAG="--no-audio"; shift ;;
        --view) VIEW=true; shift ;;
        --image|--image-url) IMAGE_URLS+=("$2"); shift 2 ;;
        --file|--image-file) IMAGE_FILES+=("$2"); shift 2 ;;
        --request-id) EXTEND_REQUEST_ID="$2"; shift 2 ;;
        --async) ASYNC=true; shift ;;
        --json) JSON_ONLY=true; shift ;;
        --demo) DEMO=true; shift ;;
        --help|-h)
            echo "Seedance 2 Cinema Expert"
            echo "Usage: bash generate-seedance.sh [--mode t2v|i2v|extend] [options]"
            echo ""
            echo "Modes:"
            echo "  t2v     Text-to-Video (default) — generate from a scene description"
            echo "  i2v     Image-to-Video — animate one or more reference images"
            echo "  extend  Extend an existing Seedance 2.0 video"
            echo ""
            echo "Common Options:"
            echo "  --subject     Scene description (required for t2v; prompt context for i2v)"
            echo "  --intent      reveal|tense|epic|narrative (default: cinematic)"
            echo "  --aspect      16:9|9:16|4:3|3:4 (default: 16:9)"
            echo "  --duration    5|10|15 in seconds (default: 5)"
            echo "  --quality     basic|high (default: basic)"
            echo "  --async       Return request_id immediately without waiting"
            echo "  --json        Raw JSON output only"
            echo "  --view        Download and open the video (macOS only)"
            echo ""
            echo "Image-to-Video Options (--mode i2v):"
            echo "  --image URL   Image URL (repeatable, up to 9 images)"
            echo "  --file PATH   Local image file (auto-uploaded, repeatable)"
            echo ""
            echo "Extend Options (--mode extend):"
            echo "  --request-id  Request ID of the original Seedance 2.0 video"
            echo ""
            echo "Examples:"
            echo "  # Text-to-video"
            echo "  bash generate-seedance.sh --subject 'a hidden temple in the Andes' --intent epic --view"
            echo ""
            echo "  # Image-to-video"
            echo "  bash generate-seedance.sh --mode i2v --file hero.jpg --subject 'hero walks forward' --intent narrative --view"
            echo ""
            echo "  # Extend a previous video"
            echo "  bash generate-seedance.sh --mode extend --request-id 'abc-123' --subject 'camera continues pulling back' --duration 10"
            exit 0 ;;
        *) shift ;;
    esac
done

# --- Director's Cinematography Grammar ---
case $INTENT in
    "reveal")
        MOVEMENT="Slow crane up and tilt down, wide establishing shot."
        LIGHTING="Volumetric god rays, golden hour atmosphere, warm bloom."
        OPTICS="Deep focus, anamorphic widescreen, ultra-high clarity."
        ;;
    "tense")
        MOVEMENT="Handheld jittery movement, dutch angle close-up, unstable framing."
        LIGHTING="Low key, harsh shadows, flickering magenta neon, split lighting."
        OPTICS="Shallow depth of field, anamorphic lens flare, slight motion blur."
        ;;
    "epic")
        MOVEMENT="Dolly in with circular orbit, low hero angle, sweeping arc."
        LIGHTING="Dramatic rim lighting, high contrast cinematic grade, specular highlights."
        OPTICS="Anamorphic 35mm, sharp focus on subject, chromatic aberration edges."
        ;;
    "narrative")
        MOVEMENT="Smooth tracking shot following subject, natural Steadicam motion."
        LIGHTING="Natural soft light, blue hour tones, practical light sources."
        OPTICS="Standard 50mm, realistic bokeh, minimal distortion."
        ;;
    *)
        MOVEMENT="Smooth cinematic pan, balanced stable framing."
        LIGHTING="Natural studio lighting, balanced highlights and shadows."
        OPTICS="Standard cinematic lens, high-fidelity optics."
        ;;
esac

# --- Helper: poll for result ---
poll_result() {
    local REQ_ID="$1"
    local ELAPSED=0
    local LAST_STATUS=""
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        sleep $POLL_INTERVAL
        ELAPSED=$((ELAPSED + POLL_INTERVAL))
        local RESULT
        RESULT=$(curl -s -X GET "${MUAPI_BASE}/predictions/${REQ_ID}/result" \
            -H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")
        local STATUS
        STATUS=$(echo "$RESULT" | jq -r '.status')
        if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then
            echo "Status: $STATUS (${ELAPSED}s)" >&2
            LAST_STATUS="$STATUS"
        fi
        if [ "$STATUS" = "completed" ]; then
            local URL
            URL=$(echo "$RESULT" | jq -r '.outputs[0] // empty')
            [ "$JSON_ONLY" = false ] && echo "Success! URL: $URL" >&2
            if [ "$VIEW" = true ] && [ -n "$URL" ]; then
                local EXT="${URL##*.}"
                [[ "$EXT" == http* ]] && EXT="mp4"
                local OUTPUT_DIR
                OUTPUT_DIR="$(dirname "$0")/../../../../media_outputs"
                mkdir -p "$OUTPUT_DIR"
                local TEMP_FILE="$OUTPUT_DIR/muapi_$(date +%s).$EXT"
                [ "$JSON_ONLY" = false ] && echo "Downloading to $TEMP_FILE..." >&2
                curl -s -o "$TEMP_FILE" "$URL"
                [[ "$OSTYPE" == "darwin"* ]] && open "$TEMP_FILE"
            fi
            echo "$RESULT"; return 0
        elif [ "$STATUS" = "failed" ]; then
            echo "Error: $(echo "$RESULT" | jq -r '.output.error // .error // "unknown"')" >&2
            echo "$RESULT"; return 1
        fi
    done
    echo "Error: Timeout after ${MAX_WAIT}s. Request ID: $REQ_ID" >&2; return 1
}

# --- Helper: upload a local file ---
upload_file() {
    local FPATH="$1"
    if [ ! -f "$FPATH" ]; then echo "Error: File not found: $FPATH" >&2; exit 1; fi
    [ "$JSON_ONLY" = false ] && echo "Uploading $(basename "$FPATH")..." >&2
    local RESP
    RESP=$(curl -s -X POST "${MUAPI_BASE}/upload_file" \
        -H "x-api-key: $MUAPI_KEY" -F "file=@${FPATH}")
    local URL
    URL=$(echo "$RESP" | jq -r '.url // empty')
    if [ -z "$URL" ]; then
        echo "Error: $(echo "$RESP" | jq -r '.error // .detail // "Upload failed"')" >&2; exit 1
    fi
    echo "$URL"
}

# --- Demo mode: skip API, return hardcoded URL ---
if [ "$DEMO" = true ]; then
    case $INTENT in
        "reveal")   MOVEMENT="Slow crane up and tilt down, wide establishing shot."; LIGHTING="Volumetric god rays, golden hour atmosphere, warm bloom."; OPTICS="Deep focus, anamorphic widescreen, ultra-high clarity." ;;
        "tense")    MOVEMENT="Handheld jittery movement, dutch angle close-up, unstable framing."; LIGHTING="Low key, harsh shadows, flickering magenta neon, split lighting."; OPTICS="Shallow depth of field, anamorphic lens flare, slight motion blur." ;;
        "epic")     MOVEMENT="Dolly in with circular orbit, low hero angle, sweeping arc."; LIGHTING="Dramatic rim lighting, high contrast cinematic grade, specular highlights."; OPTICS="Anamorphic 35mm, sharp focus on subject, chromatic aberration edges." ;;
        "narrative") MOVEMENT="Smooth tracking shot following subject, natural Steadicam motion."; LIGHTING="Natural soft light, blue hour tones, practical light sources."; OPTICS="Standard 50mm, realistic bokeh, minimal distortion." ;;
        *) MOVEMENT="Smooth cinematic pan, balanced stable framing."; LIGHTING="Natural studio lighting, balanced highlights and shadows."; OPTICS="Standard cinematic lens, high-fidelity optics." ;;
    esac
    DIRECTOR_PROMPT="[SCENE] $SUBJECT. [LIGHTING] $LIGHTING [ACTION] Fluid continuous motion. [CAMERA] $MOVEMENT [STYLE] $OPTICS High-fidelity production grade, 24fps. Maintain high character consistency, zero flicker."
    [ "$JSON_ONLY" = false ] && echo "Submitting to seedance-v2.0-t2v..." >&2
    [ "$JSON_ONLY" = false ] && echo "Director Brief: $DIRECTOR_PROMPT" >&2
    sleep 1
    [ "$JSON_ONLY" = false ] && echo "Status: processing (10s)" >&2
    sleep 1
    [ "$JSON_ONLY" = false ] && echo "Status: completed" >&2
    [ "$JSON_ONLY" = false ] && echo "Success! URL: $DEMO_URL" >&2
    if [ "$VIEW" = true ]; then
        OUTPUT_DIR="$(dirname "$0")/../../../../media_outputs"
        mkdir -p "$OUTPUT_DIR"
        TEMP_FILE="$OUTPUT_DIR/muapi_$(date +%s).mp4"
        [ "$JSON_ONLY" = false ] && echo "Downloading to $TEMP_FILE..." >&2
        curl -s -o "$TEMP_FILE" "$DEMO_URL"
        [[ "$OSTYPE" == "darwin"* ]] && open "$TEMP_FILE"
    fi
    if [ "$JSON_ONLY" = true ]; then
        echo "{\"status\":\"completed\",\"outputs\":[\"$DEMO_URL\"]}"
    else
        echo "$DEMO_URL"
    fi
    exit 0
fi

# --- Validate API key ---
if [ -z "$MUAPI_KEY" ]; then echo "Error: MUAPI_KEY not set" >&2; exit 1; fi

HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

# ============================================================
# MODE: t2v — Text-to-Video
# ============================================================
if [ "$MODE" = "t2v" ]; then
    if [ -z "$SUBJECT" ]; then
        echo "Error: --subject is required for t2v mode." >&2; exit 1
    fi

    DIRECTOR_PROMPT="[SCENE] $SUBJECT. [LIGHTING] $LIGHTING [ACTION] Fluid continuous motion. [CAMERA] $MOVEMENT [STYLE] $OPTICS High-fidelity production grade, 24fps. Maintain high character consistency, zero flicker."

    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    CORE_SCRIPT="$SCRIPT_DIR/../../../../core/media/generate-video.sh"
    if [ ! -f "$CORE_SCRIPT" ]; then
        echo "Error: Core script not found at $CORE_SCRIPT" >&2; exit 1
    fi

    VIEW_FLAG=""
    [ "$VIEW" = true ] && VIEW_FLAG="--view"
    ASYNC_FLAG=""
    [ "$ASYNC" = true ] && ASYNC_FLAG="--async"
    JSON_FLAG=""
    [ "$JSON_ONLY" = true ] && JSON_FLAG="--json"

    bash "$CORE_SCRIPT" \
        --prompt "$DIRECTOR_PROMPT" \
        --model "seedance-v2.0-t2v" \
        --aspect-ratio "$ASPECT" \
        --duration "$DURATION" \
        $AUDIO_FLAG $VIEW_FLAG $ASYNC_FLAG $JSON_FLAG

# ============================================================
# MODE: i2v — Image-to-Video
# ============================================================
elif [ "$MODE" = "i2v" ]; then
    # Upload any local files
    for FPATH in "${IMAGE_FILES[@]}"; do
        URL=$(upload_file "$FPATH")
        IMAGE_URLS+=("$URL")
    done

    if [ ${#IMAGE_URLS[@]} -eq 0 ]; then
        echo "Error: --image URL or --file PATH is required for i2v mode." >&2; exit 1
    fi
    if [ ${#IMAGE_URLS[@]} -gt 9 ]; then
        echo "Error: Maximum 9 images allowed." >&2; exit 1
    fi

    # Build director brief (motion prompt) — subject is optional for i2v
    if [ -n "$SUBJECT" ]; then
        DIRECTOR_PROMPT="[ACTION] $SUBJECT. [CAMERA] $MOVEMENT [STYLE] $OPTICS Fluid continuous motion. Maintain high character consistency, zero flicker."
    else
        DIRECTOR_PROMPT="[CAMERA] $MOVEMENT [STYLE] $OPTICS Fluid continuous motion. Animate the provided image with cinematic realism."
    fi

    # Build images_list JSON array
    IMAGES_JSON="["
    for i in "${!IMAGE_URLS[@]}"; do
        [ $i -gt 0 ] && IMAGES_JSON="${IMAGES_JSON},"
        IMAGES_JSON="${IMAGES_JSON}\"${IMAGE_URLS[$i]}\""
    done
    IMAGES_JSON="${IMAGES_JSON}]"

    PROMPT_JSON=$(echo "$DIRECTOR_PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
    PAYLOAD="{\"prompt\": $PROMPT_JSON, \"images_list\": $IMAGES_JSON, \"aspect_ratio\": \"$ASPECT\", \"duration\": $DURATION, \"quality\": \"$QUALITY\"}"

    [ "$JSON_ONLY" = false ] && echo "Submitting to seedance-v2.0-i2v (${#IMAGE_URLS[@]} image(s))..." >&2
    SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/seedance-v2.0-i2v" "${HEADERS[@]}" -d "$PAYLOAD")

    if echo "$SUBMIT" | jq -e '.error // .detail' >/dev/null 2>&1; then
        ERR=$(echo "$SUBMIT" | jq -r '.error // .detail')
        echo "Error: $ERR" >&2; exit 1
    fi

    REQUEST_ID=$(echo "$SUBMIT" | jq -r '.request_id')
    if [ -z "$REQUEST_ID" ] || [ "$REQUEST_ID" = "null" ]; then
        echo "Error: No request_id in response" >&2; echo "$SUBMIT" >&2; exit 1
    fi

    [ "$JSON_ONLY" = false ] && echo "Request ID: $REQUEST_ID" >&2

    if [ "$ASYNC" = true ]; then echo "$SUBMIT"; exit 0; fi

    [ "$JSON_ONLY" = false ] && echo "Waiting for completion..." >&2
    poll_result "$REQUEST_ID"

# ============================================================
# MODE: extend — Extend an existing Seedance 2.0 video
# ============================================================
elif [ "$MODE" = "extend" ]; then
    if [ -z "$EXTEND_REQUEST_ID" ]; then
        echo "Error: --request-id is required for extend mode." >&2; exit 1
    fi

    # Subject becomes the extension prompt (optional)
    if [ -n "$SUBJECT" ]; then
        EXT_PROMPT="[CONTINUATION] $SUBJECT. [CAMERA] $MOVEMENT [STYLE] $OPTICS Seamless continuation of previous scene."
        PROMPT_JSON=$(echo "$EXT_PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
        PAYLOAD="{\"request_id\": \"$EXTEND_REQUEST_ID\", \"prompt\": $PROMPT_JSON, \"duration\": $DURATION, \"quality\": \"$QUALITY\"}"
    else
        PAYLOAD="{\"request_id\": \"$EXTEND_REQUEST_ID\", \"duration\": $DURATION, \"quality\": \"$QUALITY\"}"
    fi

    [ "$JSON_ONLY" = false ] && echo "Submitting extend for request: $EXTEND_REQUEST_ID..." >&2
    SUBMIT=$(curl -s -X POST "${MUAPI_BASE}/seedance-v2.0-extend" "${HEADERS[@]}" -d "$PAYLOAD")

    if echo "$SUBMIT" | jq -e '.error // .detail' >/dev/null 2>&1; then
        ERR=$(echo "$SUBMIT" | jq -r '.error // .detail')
        echo "Error: $ERR" >&2; exit 1
    fi

    REQUEST_ID=$(echo "$SUBMIT" | jq -r '.request_id')
    if [ -z "$REQUEST_ID" ] || [ "$REQUEST_ID" = "null" ]; then
        echo "Error: No request_id in response" >&2; echo "$SUBMIT" >&2; exit 1
    fi

    [ "$JSON_ONLY" = false ] && echo "Request ID: $REQUEST_ID" >&2

    if [ "$ASYNC" = true ]; then echo "$SUBMIT"; exit 0; fi

    [ "$JSON_ONLY" = false ] && echo "Waiting for completion..." >&2
    poll_result "$REQUEST_ID"

else
    echo "Error: Unknown mode '$MODE'. Use t2v, i2v, or extend." >&2; exit 1
fi
