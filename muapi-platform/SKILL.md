---
name: muapi-platform
description: muapi.ai platform utilities — API key setup, file upload, and prediction polling. Use when user asks "Setup my API key", "Check result of request", "Upload a file to CDN", "What is the status of my generation", or needs to configure the muapi.ai integration.
metadata:
  author: muapi-ai
  version: "1.0.0"
---

# muapi.ai Platform

Platform utilities for API key management, file upload, and prediction status checking.

## Scripts

| Script | Purpose |
|--------|---------|
| `setup.sh` | Configure MUAPI_KEY and show current config |
| `check-result.sh` | Poll a prediction by request ID until complete |

---

## Setup

```bash
bash /mnt/skills/user/muapi-platform/scripts/setup.sh [options]
```

### Configure API Key

```bash
# Interactive setup
bash setup.sh --add-key

# Set key directly
bash setup.sh --add-key "your_api_key_here"

# Show current configuration
bash setup.sh --show-config
```

This saves `MUAPI_KEY` to your `.env` file for persistent use across all muapi scripts.

**Getting your API key:**
1. Go to [muapi.ai/dashboard](https://muapi.ai/dashboard)
2. Navigate to API Keys
3. Create a new key

---

## Check Result

```bash
bash /mnt/skills/user/muapi-platform/scripts/check-result.sh --id REQUEST_ID
```

Poll a previously submitted prediction until it completes.

### Examples

```bash
# Poll until complete (blocks, waits up to 10 minutes)
bash check-result.sh --id "abc123-def456"

# Just check current status (non-blocking)
bash check-result.sh --id "abc123-def456" --once

# Raw JSON output
bash check-result.sh --id "abc123-def456" --json

# Custom timeout
bash check-result.sh --id "abc123-def456" --timeout 120
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--id` | Request ID to poll (required) | — |
| `--once` | Check once and return (no polling) | — |
| `--timeout` | Max wait seconds | `600` |
| `--json` | Raw JSON output | — |

### Output

**Pending:**
```
Status: processing
Elapsed: 15s
```

**Complete:**
```
Status: completed
Result URL: https://cdn.muapi.ai/files/abc123/output.mp4
```

**Failed:**
```
Status: failed
Error: Model error: out of memory
```

---

## API Reference

**Base URL:** `https://api.muapi.ai/api/v1`

**Authentication:** All requests require the header:
```
x-api-key: YOUR_MUAPI_KEY
```

**Common Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/predictions/{id}/result` | GET | Poll generation result |
| `/upload_file` | POST | Upload file (multipart/form-data) |
| `/get_upload_url?filename=...` | GET | Get presigned upload URL |

**Result Response:**
```json
{
  "request_id": "abc123-def456",
  "status": "completed",
  "outputs": [
    "https://cdn.muapi.ai/files/abc123/output.mp4"
  ]
}
```

**Status values:** `pending`, `processing`, `completed`, `failed`

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MUAPI_KEY` | Your muapi.ai API key |

```bash
# Option 1: Export directly
export MUAPI_KEY=your_key_here

# Option 2: .env file (auto-loaded by all scripts)
echo "MUAPI_KEY=your_key" >> .env

# Option 3: Use --add-key flag
bash setup.sh --add-key
```

## Troubleshooting

### Authentication Error (401)
```
Error: Unauthorized
```
Your API key is invalid or expired. Get a new key at [muapi.ai/dashboard](https://muapi.ai/dashboard).

### Rate Limit (429)
```
Error: Too many requests
```
You've exceeded your plan's rate limit. Wait a moment and retry.

### Prediction Not Found (404)
```
Error: Prediction not found
```
The request_id may be incorrect, or the result has expired. Results are typically available for 24 hours.
