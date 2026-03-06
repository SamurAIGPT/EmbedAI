#!/bin/bash
# Assistant Skill: Interactive Workflow Runner
# Thin wrapper around muapi CLI.

WORKFLOW_ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --workflow-id|-w) WORKFLOW_ID="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$WORKFLOW_ID" ]; then echo "Error: --workflow-id is required" >&2; exit 1; fi

muapi workflow run-interactive "$WORKFLOW_ID"
