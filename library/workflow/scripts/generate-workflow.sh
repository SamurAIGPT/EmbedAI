#!/bin/bash
# Expert Skill: AI Workflow Architect
# Generate a multi-step AI workflow from a natural language description.
# The AI architect translates your intent into a connected node graph.

PROMPT=""
WORKFLOW_ID=""   # Set to edit an existing workflow
SYNC=true
VIEW=false
JSON_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p)       PROMPT="$2"; shift 2 ;;
        --workflow-id|-w)  WORKFLOW_ID="$2"; shift 2 ;;
        --async)           SYNC=false; shift ;;
        --view)            VIEW=true; shift ;;
        --json)            JSON_ONLY=true; shift ;;
        --help|-h)
            echo "AI Workflow Architect — generate or edit a workflow from a description"
            echo ""
            echo "Usage:"
            echo "  bash generate-workflow.sh --prompt \"description\""
            echo "  bash generate-workflow.sh --prompt \"add upscale after image gen\" --workflow-id abc123"
            echo ""
            echo "Options:"
            echo "  --prompt         Natural language workflow description (required)"
            echo "  --workflow-id    Existing workflow ID to edit (omit to create new)"
            echo "  --async          Return request_id immediately without waiting"
            echo "  --view           Open result in browser after creation"
            echo "  --json           Raw JSON output only"
            echo ""
            echo "Examples:"
            echo "  # Create a new workflow"
            echo "  bash generate-workflow.sh \\"
            echo "    --prompt 'take a text prompt, generate with flux-dev, then upscale the result'"
            echo ""
            echo "  # Edit an existing workflow"
            echo "  bash generate-workflow.sh \\"
            echo "    --prompt 'add a face-swap step after the image generation' \\"
            echo "    --workflow-id abc123"
            echo ""
            echo "  # Inspect the result immediately"
            echo "  bash generate-workflow.sh \\"
            echo "    --prompt 'text → video with kling → lipsync audio' \\"
            echo "    --view"
            exit 0 ;;
        *) shift ;;
    esac
done

if [ -z "$PROMPT" ]; then
    echo "Error: --prompt is required" >&2
    exit 1
fi

# Build JSON body
if [ -n "$WORKFLOW_ID" ]; then
    BODY="{\"prompt\": $(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))'), \"workflow_id\": \"$WORKFLOW_ID\", \"sync\": $SYNC}"
    [ "$JSON_ONLY" = false ] && echo "Editing workflow $WORKFLOW_ID..." >&2
else
    BODY="{\"prompt\": $(echo "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip()))'), \"sync\": $SYNC}"
    [ "$JSON_ONLY" = false ] && echo "Generating workflow..." >&2
fi

MUAPI_HOST="${MUAPI_BASE_URL:-https://api.muapi.ai}"
RESULT=$(curl -s -X POST "${MUAPI_HOST}/workflow/architect" \
    -H "x-api-key: $MUAPI_KEY" \
    -H "Content-Type: application/json" \
    -d "$BODY")

if [ "$SYNC" = false ]; then
    REQUEST_ID=$(echo "$RESULT" | jq -r '.request_id // empty')
    if [ -n "$REQUEST_ID" ]; then
        [ "$JSON_ONLY" = false ] && echo "Generation started. Request ID: $REQUEST_ID" >&2
        [ "$JSON_ONLY" = false ] && echo "Poll: muapi workflow poll $REQUEST_ID" >&2
    fi
    echo "$RESULT"
    exit 0
fi

# Check for error
if echo "$RESULT" | jq -e '.detail // .error' >/dev/null 2>&1; then
    ERR=$(echo "$RESULT" | jq -r '.detail // .error')
    echo "Error: $ERR" >&2
    echo "$RESULT" >&2
    exit 1
fi

WF_ID=$(echo "$RESULT" | jq -r '.workflow.id // .id // empty')
WF_NAME=$(echo "$RESULT" | jq -r '.workflow.name // .name // empty')
NODE_COUNT=$(echo "$RESULT" | jq -r '[.workflow.nodes // .nodes // []] | length')

if [ "$JSON_ONLY" = false ]; then
    echo "" >&2
    echo "Workflow ready!" >&2
    echo "  ID:    $WF_ID" >&2
    echo "  Name:  $WF_NAME" >&2
    echo "  Nodes: $NODE_COUNT" >&2
    echo "" >&2
    echo "To run it:" >&2
    echo "  muapi workflow run $WF_ID" >&2
    echo "  bash run-workflow.sh --workflow-id $WF_ID" >&2
fi

if [ "$VIEW" = true ] && [ -n "$WF_ID" ]; then
    URL="${MUAPI_HOST}/workflow/$WF_ID"
    [ "$JSON_ONLY" = false ] && echo "Opening in browser: $URL" >&2
    [[ "$OSTYPE" == "darwin"* ]] && open "$URL"
fi

echo "$RESULT"
