#!/bin/bash
# Expert Skill: AI Workflow Architect
# Thin wrapper around muapi CLI.

PROMPT=""
WORKFLOW_ID=""   # Set to edit an existing workflow
ASYNC=false
VIEW=false
JSON_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p)       PROMPT="$2"; shift 2 ;;
        --workflow-id|-w)  WORKFLOW_ID="$2"; shift 2 ;;
        --async)           ASYNC=true; shift ;;
        --view)            VIEW=true; shift ;;
        --json)            JSON_ONLY=true; shift ;;
        *) shift ;;
    esac
done

if [ -z "$PROMPT" ]; then echo "Error: --prompt is required" >&2; exit 1; fi

ARGS=()
[ "$ASYNC" = true ] && ARGS+=("--async")
[ "$VIEW" = true ] && ARGS+=("--view")
[ "$JSON_ONLY" = true ] && ARGS+=("--output-json")

if [ -n "$WORKFLOW_ID" ]; then
    muapi workflow edit "$WORKFLOW_ID" --prompt "$PROMPT" "${ARGS[@]}"
else
    muapi workflow create "$PROMPT" "${ARGS[@]}"
fi
