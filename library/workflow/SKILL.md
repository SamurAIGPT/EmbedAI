---
name: muapi-workflow
description: Build, run, and visualize multi-step AI generation workflows. The AI architect translates natural language descriptions into connected node graphs — chain image generation, video creation, enhancement, and editing into automated pipelines.
---

# AI Workflow Builder

Chain any combination of muapi.ai generation steps into automated pipelines. The AI architect converts your plain-language description into a runnable node graph.

## Core Operations

1. **Generate** (`generate-workflow.sh`) — AI architect creates a workflow from a description
2. **Edit** (`generate-workflow.sh --workflow-id`) — Modify an existing workflow with a prompt
3. **Run** (`run-workflow.sh`) — Execute a workflow, poll node-by-node, collect outputs
4. **CLI** (`muapi workflow`) — Full CRUD + visualization directly from the terminal

---

## Protocol: Building a Workflow

### Step 1 — Describe your pipeline

```bash
bash scripts/generate-workflow.sh \
  --prompt "take a text prompt, generate an image with flux-dev, then upscale it to 4K"
```

The architect returns a workflow with a unique ID and a node graph. Save the ID.

### Step 2 — Inspect and visualize

```bash
# Rich ASCII node graph in the terminal
muapi workflow get <workflow_id>

# Or raw JSON
muapi workflow get <workflow_id> --output-json
```

Output shows each node, its type, parameters, and connections:
```
[text-passthrough]
  id: node1

        node1 ──► node2

[flux-dev: image-generate]        [ai-image-upscale]
  id: node2                         id: node3
  prompt: (from node1)

        node2 ──► node3
```

### Step 3 — Run it

```bash
# Run with specific inputs
muapi workflow execute <workflow_id> \
  --input "node1.prompt=a glowing crystal cave at midnight"

# Or with the shell script
bash scripts/run-workflow.sh \
  --workflow-id <workflow_id> \
  --input "node1.prompt=a glowing crystal cave at midnight" \
  --download ./outputs \
  --view
```

---

## Workflow Prompt Examples

### Image Pipelines

```bash
# Text → Image → Upscale
bash scripts/generate-workflow.sh \
  --prompt "take a text prompt, generate with flux-dev, upscale the result"

# Text → Image → Background removal → Product shot
bash scripts/generate-workflow.sh \
  --prompt "generate a product image with hidream, remove background, create professional product shot"

# Image + text → Style transfer → Enhance
bash scripts/generate-workflow.sh \
  --prompt "take an input image, apply ghibli style, then upscale"
```

### Video Pipelines

```bash
# Text → Video
bash scripts/generate-workflow.sh \
  --prompt "generate a 10-second cinematic video from a text prompt using kling-master"

# Image → Video → Lipsync
bash scripts/generate-workflow.sh \
  --prompt "animate an input image with seedance, then apply lipsync from an audio file"

# Text → Image → Video → Effects
bash scripts/generate-workflow.sh \
  --prompt "generate an image with flux, animate it with kling, apply cinematic video effects"
```

### Audio Pipelines

```bash
# Text → Music → Video with music
bash scripts/generate-workflow.sh \
  --prompt "generate background music with suno, then create a looping video that matches the vibe"
```

---

## Editing an Existing Workflow

```bash
# Add a step
bash scripts/generate-workflow.sh \
  --prompt "add a face-swap step after the image generation" \
  --workflow-id <id>

# Swap a model
bash scripts/generate-workflow.sh \
  --prompt "change the video model from kling to veo3" \
  --workflow-id <id>

# Remove a step
bash scripts/generate-workflow.sh \
  --prompt "remove the upscale node and connect the image directly to the output" \
  --workflow-id <id>
```

---

## CLI Reference

```bash
# List all your workflows
muapi workflow list

# Browse templates
muapi workflow templates

# Generate new workflow
muapi workflow create "text → flux image → upscale → face swap"

# Visualize a workflow
muapi workflow get <id>

# Execute with inputs
muapi workflow execute <id> --input "node1.prompt=a sunset"

# Monitor a run
muapi workflow status <run_id>

# Get outputs
muapi workflow outputs <run_id> --download ./results

# Edit with AI
muapi workflow edit <id> --prompt "add lipsync at the end"

# Rename / delete
muapi workflow rename <id> --name "Product Pipeline v2"
muapi workflow delete <id>
```

---

## MCP Tools (for AI agents)

| Tool | Description |
|------|-------------|
| `muapi_workflow_list` | List user's workflows |
| `muapi_workflow_create` | AI architect: prompt → workflow |
| `muapi_workflow_get` | Get workflow definition + node graph |
| `muapi_workflow_execute` | Run with specific inputs |
| `muapi_workflow_status` | Node-by-node run status |
| `muapi_workflow_outputs` | Final output URLs |

---

## Constraints

- Workflows can contain any combination of muapi.ai nodes (image, video, audio, enhance, edit)
- Node outputs are automatically wired as inputs to downstream nodes
- `--sync` mode waits up to 120s for generation; use `--async` for complex workflows and poll separately
- Run timeouts: 10 minutes maximum per workflow execution
