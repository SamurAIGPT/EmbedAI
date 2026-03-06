#!/bin/bash
# Expert Skill: Run & Visualize an AI Workflow
# Thin wrapper around muapi CLI.

WORKFLOW_ID=""
INPUT_ARGS=()
WEBHOOK=""
ASYNC=false
DOWNLOAD_DIR=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --workflow-id|-w)  WORKFLOW_ID="$2"; shift 2 ;;
        --input|-i)        INPUT_ARGS+=("$2"); shift 2 ;;
        --webhook)         WEBHOOK="$2"; shift 2 ;;
        --async)           ASYNC=true; shift ;;
        --download|-d)     DOWNLOAD_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$WORKFLOW_ID" ]; then echo "Error: --workflow-id is required" >&2; exit 1; fi

ARGS=()
for ITEM in "${INPUT_ARGS[@]}"; do ARGS+=("--input" "$ITEM"); done
[ -n "$WEBHOOK" ] && ARGS+=("--webhook" "$WEBHOOK")
[ "$ASYNC" = true ] && ARGS+=("--no-wait")
[ -n "$DOWNLOAD_DIR" ] && ARGS+=("--download" "$DOWNLOAD_DIR")

if [ ${#INPUT_ARGS[@]} -gt 0 ]; then
    muapi workflow execute "$WORKFLOW_ID" "${ARGS[@]}"
else
    muapi workflow run "$WORKFLOW_ID" "${ARGS[@]}"
fi
