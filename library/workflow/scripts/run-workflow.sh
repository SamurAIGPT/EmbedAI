#!/bin/bash
# Expert Skill: Run & Visualize an AI Workflow
# Execute a saved workflow, poll node-by-node status, and display outputs.

WORKFLOW_ID=""
INPUT_ARGS=()
WEBHOOK=""
ASYNC=false
JSON_ONLY=false
DOWNLOAD_DIR=""
VIEW=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --workflow-id|-w)  WORKFLOW_ID="$2"; shift 2 ;;
        --input|-i)        INPUT_ARGS+=("$2"); shift 2 ;;
        --webhook)         WEBHOOK="$2"; shift 2 ;;
        --async)           ASYNC=true; shift ;;
        --json)            JSON_ONLY=true; shift ;;
        --download|-d)     DOWNLOAD_DIR="$2"; shift 2 ;;
        --view)            VIEW=true; shift ;;
        --help|-h)
            echo "Run an AI Workflow"
            echo ""
            echo "Usage:"
            echo "  bash run-workflow.sh --workflow-id ID [--input node_id.param=value ...]"
            echo ""
            echo "Options:"
            echo "  --workflow-id    Workflow ID to run (required)"
            echo "  --input          node_id.param=value (repeatable) for api-execute mode"
            echo "  --webhook        Webhook URL for completion notification"
            echo "  --async          Return run_id without waiting"
            echo "  --download DIR   Download output files to directory"
            echo "  --view           Open output URLs (macOS)"
            echo "  --json           Raw JSON output only"
            echo ""
            echo "Examples:"
            echo "  # Run with last saved inputs"
            echo "  bash run-workflow.sh --workflow-id abc123"
            echo ""
            echo "  # Run with specific inputs"
            echo "  bash run-workflow.sh --workflow-id abc123 \\"
            echo "    --input 'text-node.prompt=a glowing crystal cave'"
            echo ""
            echo "  # Run async, then check later"
            echo "  bash run-workflow.sh --workflow-id abc123 --async"
            echo "  muapi workflow status <run_id>"
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$WORKFLOW_ID" ]; then
    echo "Error: --workflow-id is required" >&2
    exit 1
fi

MUAPI_HOST="${MUAPI_BASE_URL:-https://api.muapi.ai}"
HEADERS=(-H "x-api-key: $MUAPI_KEY" -H "Content-Type: application/json")

# ── Execute mode: specific inputs supplied ────────────────────────────────────
if [ ${#INPUT_ARGS[@]} -gt 0 ]; then
    # Build inputs JSON: {"node_id": {"param": "value"}}
    INPUTS_JSON="{}"
    for ITEM in "${INPUT_ARGS[@]}"; do
        if [[ "$ITEM" != *"."* ]] || [[ "$ITEM" != *"="* ]]; then
            echo "Error: --input must be in format node_id.param=value" >&2
            exit 1
        fi
        KEY="${ITEM%%=*}"
        VALUE="${ITEM#*=}"
        NODE_ID="${KEY%%.*}"
        PARAM="${KEY#*.}"
        VALUE_JSON=$(echo "$VALUE" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))')
        INPUTS_JSON=$(echo "$INPUTS_JSON" | python3 -c "
import json,sys
d = json.load(sys.stdin)
d.setdefault('$NODE_ID', {})['$PARAM'] = $VALUE_JSON
print(json.dumps(d))")
    done

    BODY="{\"inputs\": $INPUTS_JSON"
    [ -n "$WEBHOOK" ] && BODY="$BODY, \"webhook_url\": \"$WEBHOOK\""
    BODY="$BODY}"

    [ "$JSON_ONLY" = false ] && echo "Executing workflow $WORKFLOW_ID with inputs..." >&2
    RESULT=$(curl -s -X POST "${MUAPI_HOST}/workflow/${WORKFLOW_ID}/api-execute" "${HEADERS[@]}" -d "$BODY")
else
    # ── Simple run mode ───────────────────────────────────────────────────────
    BODY="{}"
    [ -n "$WEBHOOK" ] && BODY="{\"webhook_url\": \"$WEBHOOK\"}"

    [ "$JSON_ONLY" = false ] && echo "Starting workflow run $WORKFLOW_ID..." >&2
    RESULT=$(curl -s -X POST "${MUAPI_HOST}/workflow/${WORKFLOW_ID}/run" "${HEADERS[@]}" -d "$BODY")
fi

# Check for errors
if echo "$RESULT" | jq -e '.detail // .error' >/dev/null 2>&1; then
    ERR=$(echo "$RESULT" | jq -r '.detail // .error')
    echo "Error: $ERR" >&2
    exit 1
fi

RUN_ID=$(echo "$RESULT" | jq -r '.run_id // .id // empty')
[ "$JSON_ONLY" = false ] && echo "Run started. run_id: $RUN_ID" >&2

if [ "$ASYNC" = true ]; then
    echo "$RESULT"
    exit 0
fi

if [ -z "$RUN_ID" ]; then
    echo "$RESULT"
    exit 0
fi

# ── Poll for completion ───────────────────────────────────────────────────────
MAX_WAIT=600
ELAPSED=0
POLL_INTERVAL=5
LAST_STATUS=""

[ "$JSON_ONLY" = false ] && echo "Polling run status..." >&2

while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    STATUS_RESP=$(curl -s "${MUAPI_HOST}/workflow/run/${RUN_ID}/status" "${HEADERS[@]}")
    STATUS=$(echo "$STATUS_RESP" | jq -r '.status // empty')

    if [ "$STATUS" != "$LAST_STATUS" ] && [ "$JSON_ONLY" = false ]; then
        echo "  [$((ELAPSED))s] status: $STATUS" >&2

        # Print node-level statuses when available
        NODE_COUNT=$(echo "$STATUS_RESP" | jq '[.nodes // .node_statuses // []] | length')
        if [ "$NODE_COUNT" -gt 0 ] 2>/dev/null; then
            echo "$STATUS_RESP" | jq -r '(.nodes // .node_statuses // [])[] | "    \(.id) [\(.type // "node")] → \(.status)"' 2>/dev/null >&2 || true
        fi
        LAST_STATUS="$STATUS"
    fi

    if [ "$STATUS" = "completed" ]; then
        [ "$JSON_ONLY" = false ] && echo "" >&2
        [ "$JSON_ONLY" = false ] && echo "Workflow completed!" >&2

        # Fetch outputs
        OUT_RESP=$(curl -s "${MUAPI_HOST}/workflow/run/${RUN_ID}/api-outputs" "${HEADERS[@]}")

        if [ "$JSON_ONLY" = true ]; then
            echo "$OUT_RESP"
            exit 0
        fi

        # Print output URLs
        URLS=$(echo "$OUT_RESP" | jq -r '(.outputs // [])[] | if type == "string" then . else (.outputs // [])[] end' 2>/dev/null)
        if [ -n "$URLS" ]; then
            echo "" >&2
            echo "Outputs:" >&2
            echo "$URLS" | while read -r URL; do
                echo "  $URL" >&2

                # Download if requested
                if [ -n "$DOWNLOAD_DIR" ]; then
                    mkdir -p "$DOWNLOAD_DIR"
                    FNAME=$(basename "${URL%%\?*}")
                    [ -z "$FNAME" ] && FNAME="output_$(date +%s)"
                    DEST="$DOWNLOAD_DIR/$FNAME"
                    echo "  → Downloading to $DEST" >&2
                    curl -s -o "$DEST" "$URL"
                fi

                # Open on macOS
                if [ "$VIEW" = true ] && [[ "$OSTYPE" == "darwin"* ]]; then
                    if [ -n "$DOWNLOAD_DIR" ] && [ -f "$DEST" ]; then
                        open "$DEST"
                    else
                        open "$URL"
                    fi
                fi
            done
        fi

        echo "$OUT_RESP"
        exit 0

    elif [ "$STATUS" = "failed" ]; then
        echo "Error: Workflow run failed" >&2
        echo "$STATUS_RESP" >&2
        exit 1
    fi
done

echo "Error: Timeout after ${MAX_WAIT}s. run_id: $RUN_ID" >&2
exit 1
