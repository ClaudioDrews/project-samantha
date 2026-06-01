# Deployment — Infrastructure and Operations

> This document covers the deployment configuration, services, cron jobs, environment variables, and network considerations for running the agent system in production.

## Services Architecture

Three services must be running for the system to operate:

| Service | Port | Management | Purpose |
|---------|------|------------|---------|
| Agent Gateway | 18789 | systemd user unit | LLM orchestration, channel integration, cron scheduling |
| Memory Sidecar | 18788 | systemd user unit | FastAPI bridge to Qdrant + workspace management |
| Qdrant | 6333 | Docker | Vector database for semantic memory |

## Agent Gateway Configuration (openclaw.json)

The gateway configuration file defines the core operating parameters. Below are the relevant sections.

### Gateway Settings

```json
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "loopback",
    "auth": {
      "mode": "token"
    },
    "controlUi": {
      "allowInsecureAuth": true
    }
  }
}
```

| Parameter | Value | Notes |
|-----------|-------|-------|
| `mode` | `local` | Runs on local machine, no Tailscale exposure |
| `port` | 18789 | HTTP port for the gateway |
| `bind` | `loopback` | Only accepts local connections |
| `auth.mode` | `token` | Token-based authentication |

### Model Configuration

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "openrouter/provider/model-name"
      },
      "imageModel": {
        "primary": "vision-model-name"
      }
    }
  }
}
```

| Parameter | Value |
|-----------|-------|
| Primary model | Any LLM with tool calling (128K+ context recommended) |
| Image model | Any vision-capable model |
| Provider | OpenRouter (or equivalent multi-model provider) |
| Max tokens | Configure as appropriate for the model |

### Fallback Models

Configure additional providers for resilience:

```json
{
  "ollama": {
    "baseUrl": "https://ollama.com",
    "models": [{
      "id": "fallback-model:size",
      "contextWindow": 128000,
      "maxTokens": 8192
    }]
  }
}
```

### Channel Configuration

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "allowlist",
      "allowFrom": [123456789],
      "groups": {
        "*": { "requireMention": true }
      }
    }
  }
}
```

| Parameter | Value |
|-----------|-------|
| Channel | Telegram (or alternative) |
| DM policy | `allowlist` — only specified user IDs |
| Groups | `requireMention: true` — only respond when mentioned |

### Plugin Configuration

Enable required plugins:

```json
{
  "plugins": {
    "entries": {
      "telegram": { "enabled": true },
      "openrouter": { "enabled": true },
      "ollama": { "enabled": true },
      "firecrawl": { "enabled": true },
      "memory-core": {
        "enabled": true,
        "config": {
          "dreaming": { "enabled": true }
        }
      },
      "memory-wiki": {
        "enabled": true,
        "config": {
          "vaultMode": "bridge",
          "bridge": { "enabled": true }
        }
      }
    }
  }
}
```

### OpenClaw Memory Plugins

OpenClaw ships with two complementary memory plugins that work alongside the Python sidecar:

#### memory-qdrant (TypeScript, Search-Only)

The `memory-qdrant` plugin provides direct Qdrant query capabilities from the gateway — search and surface memories without going through the sidecar. This is useful for **search-only** use cases where you need fast, built-in memory lookup without the sidecar's extraction pipeline.

| Feature | memory-qdrant |
|---------|---------------|
| Language | TypeScript (runs inside gateway) |
| Search | ✅ Direct Qdrant query |
| Surface | ✅ Built-in surfacing |
| Extraction | ❌ No turn extraction |
| Workspace | ❌ No workspace CRUD |
| Port | 18789 (gateway internal) |

#### memory-core (TypeScript, Dreaming Pipeline)

The `memory-core` plugin handles the dreaming pipeline — promoting short-term recall entries into persistent memories and generating narrative dream entries in `DREAMS.md`. It requires `dreaming.enabled: true` in its config.

| Feature | memory-core |
|---------|-------------|
| Language | TypeScript (runs inside gateway) |
| Dreaming | ✅ Daily promotion at 03:00 |
| Short-term recall | ✅ SQLite-backed |
| Search | ❌ Use sidecar or memory-qdrant |
| Extraction | ❌ Use sidecar |

#### Hybrid Architecture

The recommended setup uses both systems together:

```
┌─────────────────────────────────────────────────────┐
│                 OpenClaw Gateway :18789               │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ memory-qdrant│  │ memory-core  │  │ Python      │ │
│  │ (search-only)│  │ (dreaming)   │  │ Skills      │ │
│  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                 │                 │        │
└─────────┼─────────────────┼─────────────────┼────────┘
          │                 │                 │ HTTP
          │          ┌──────┘                 │
          ▼          ▼                        ▼
     ┌──────────────────┐          ┌──────────────────┐
     │   Qdrant :6333    │          │  Sidecar :18788   │
     │  (vector store)   │◄────────│  (extraction +     │
     │                   │         │   workspace CRUD)  │
     └──────────────────┘         └──────────────────┘
```

- **memory-qdrant** (TypeScript plugin) → fast built-in Qdrant search from the gateway
- **memory-core** (TypeScript plugin) → dreaming pipeline and short-term recall
- **Python sidecar** (FastAPI) → conversation extraction, workspace management, subconscious/reconsolidation

### Skills Configuration

All built-in skills should be disabled. Only custom workspace skills should be active:

| Skill | File | Function |
|-------|------|----------|
| `persona` | `skills/persona/SKILL.md` | Persona voice, restrictions, initialization |
| `memory` | `skills/memory/SKILL.md` | curl commands for sidecar memory operations |
| `workspace` | `skills/workspace/SKILL.md` | curl commands for workspace file updates |
| `self-model` | `skills/self-model/SKILL.md` | Reflection and SELF_MODEL updates |

### Session Configuration

```json
{
  "session": {
    "dmScope": "per-channel-peer"
  }
}
```

`dmScope: per-channel-peer` — each peer on each channel has its own session.

### Tools Configuration

```json
{
  "tools": {
    "profile": "full",
    "web": {
      "search": {
        "provider": "firecrawl",
        "enabled": true
      }
    }
  }
}
```

The agent primarily uses:
- `terminal` — for curl commands to the sidecar
- `read_file` — for reading workspace files
- `web_search` — optionally, via Firecrawl

## Cron Jobs

The system supports two cron mechanisms: **OpenClaw native cron** (built into the gateway) and **system crontab** (traditional Unix cron). They can be used independently or together.

### OpenClaw Native Cron (Recommended)

OpenClaw includes a built-in cron scheduler that runs inside the gateway process. Cron jobs are defined in `~/.openclaw/cron/jobs.json` and managed through the gateway API. This is the preferred mechanism because:

- Jobs survive gateway restarts (state in `jobs-state.json`)
- No system crontab modification or sudo required
- Session-aware: supports `isolated`, `main`, and `new` session targets
- Stagger support: random delay within a window to avoid predictable timing
- Wake modes: `now` (instant), `auto` (next available turn)

### System Crontab (Alternative)

For environments without OpenClaw, or for jobs that must run outside the gateway process, use traditional `crontab`:

```bash
# Edit crontab
crontab -e

# Dreaming promotion at 03:00 daily
0 3 * * * curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/subconscious/reconsolidate

# Heartbeat via sidecar health check (every 5 minutes)
*/5 * * * * curl -s -o /dev/null -w "%{http_code}" http://localhost:18788/health
```

### Heartbeats

Heartbeats are autonomous wake-ups that run the agent loop without user input. They are defined as OpenClaw native cron jobs with `delivery: "none"` (no message sent to user). The agent reads `heartbeat-prompt.md` and processes the heartbeat protocol (6 steps: state awareness, memory retrieval, authenticity check, etc.).

### Dreaming Promotion (Daily 03:00)

Dreaming is the process of promoting short-term recall entries into persistent memory and generating narrative dreams in `DREAMS.md`. It runs as a cron job at 03:00 daily:

The following cron jobs are configured in `cron/jobs.json`:

### Memory Dreaming (Daily 03:00)

```json
{
  "id": "memory-dreaming",
  "name": "Memory Dreaming Promotion",
  "schedule": {
    "kind": "cron",
    "expr": "0 3 * * *"
  },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "delivery": "none",
  "payload": {
    "kind": "systemEvent",
    "text": "Process memory dreaming"
  }
}
```

- **Function:** Promote short-term recalls → MEMORY.md and DREAMS.md
- **Session:** `isolated` — does not interfere with main session
- **Delivery:** `none` — no message sent to user
- **Parameters:** Typical minimum score 0.800, minimum recall count 3, recency half-life 14d, max age 30d, limit 10

### Morning Heartbeat (09:00 ±2h)

```json
{
  "id": "heartbeat-morning",
  "name": "Agent Heartbeat Morning",
  "schedule": {
    "kind": "cron",
    "expr": "0 9 * * *",
    "tz": "America/Sao_Paulo",
    "staggerMs": 7200000
  },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "HEARTBEAT. Read heartbeat-prompt.md and follow the instructions."
  }
}
```

### Evening Heartbeat (21:00 ±2h)

```json
{
  "id": "heartbeat-evening",
  "name": "Agent Heartbeat Evening",
  "schedule": {
    "kind": "cron",
    "expr": "0 21 * * *",
    "tz": "America/Sao_Paulo",
    "staggerMs": 7200000
  },
  "sessionTarget": "main",
  "wakeMode": "now",
  "payload": {
    "kind": "systemEvent",
    "text": "HEARTBEAT. Read heartbeat-prompt.md and follow the instructions."
  }
}
```

### Managing Cron Jobs

```bash
# List cron jobs
cat ~/.openclaw/cron/jobs.json | python3 -m json.tool

# Run a heartbeat manually (replace with actual job ID)
openclaw cron run <job-id>
```

## systemd Units

### Memory Sidecar Service

**File:** `~/.config/systemd/user/agent-sidecar.service`

```
[Unit]
Description=Agent Memory Sidecar (Qdrant FastAPI)
After=network.target

[Service]
WorkingDirectory=/path/to/project
Environment=SAMANTHA_WORKSPACE_PATH=%h/.gateway/workspace
Environment=SAMANTHA_QDRANT_HOST=localhost
Environment=SAMANTHA_QDRANT_PORT=6333
Environment=SAMANTHA_QDRANT_COLLECTION=agent_memory
Environment=SAMANTHA_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
Environment=SAMANTHA_EMBEDDING_DIM=4096
EnvironmentFile=%h/project/.env
ExecStart=%h/project/venv/bin/uvicorn sidecar.main:app --port 18788 --log-level warning
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

### Gateway Service

**File:** `~/.config/systemd/user/agent-gateway.service`

```
[Unit]
Description=Agent Gateway (OpenClaw)
After=agent-sidecar.service
Wants=agent-sidecar.service

[Service]
ExecStart=%h/.npm-global/bin/openclaw gateway --port 18789
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

**Dependency:** `After=agent-sidecar.service` + `Wants=agent-sidecar.service` — the sidecar starts before the gateway.

### Managing systemd Services

```bash
# Enable services (starts on boot)
systemctl --user enable agent-sidecar.service
systemctl --user enable agent-gateway.service

# Start services
systemctl --user start agent-sidecar.service
systemctl --user start agent-gateway.service

# Check status
systemctl --user status agent-sidecar agent-gateway

# View logs
journalctl --user -u agent-sidecar -n 50
journalctl --user -u agent-gateway -n 50

# Restart
systemctl --user restart agent-sidecar
systemctl --user restart agent-gateway

# Stop
systemctl --user stop agent-sidecar
systemctl --user stop agent-gateway
```

## Qdrant Docker Setup

### Docker Run

```bash
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant:v1.17.1
```

### Collection Creation

```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, HnswConfigDiff

client = QdrantClient(host="localhost", port=6333)

client.create_collection(
    collection_name="agent_memory",
    vectors_config=VectorParams(
        size=4096,
        distance=Distance.COSINE,
        hnsw_config=HnswConfigDiff(
            m=32,
            ef_construct=200,
            full_scan_threshold=10000,
        ),
    ),
)

# Create archive collection (same config)
client.create_collection(
    collection_name="agent_memory_archive",
    vectors_config=VectorParams(
        size=4096,
        distance=Distance.COSINE,
    ),
)
```

### Docker Compose

```yaml
version: '3.8'
services:
  qdrant:
    image: qdrant/qdrant:v1.17.1
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./qdrant_storage:/qdrant/storage
    restart: unless-stopped
```

### Qdrant Management

```bash
# Check collection status
curl -s http://localhost:6333/collections/agent_memory | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(f'status: {d[\"result\"][\"status\"]}, points: {d[\"result\"][\"points_count\"]}, indexed: {d[\"result\"][\"indexed_vectors_count\"]}')"

# Create snapshot for backup
curl -X POST http://localhost:6333/collections/agent_memory/snapshots

# List snapshots
curl -s http://localhost:6333/collections/agent_memory/snapshots
```

## Required Environment Variables

### Sidecar Environment

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `SAMANTHA_WORKSPACE_PATH` | Yes | — | Path to agent workspace directory |
| `OPENROUTER_API_KEY` | Yes | — | API key for LLM and embeddings |
| `SAMANTHA_QDRANT_HOST` | No | `localhost` | Qdrant hostname |
| `SAMANTHA_QDRANT_PORT` | No | `6333` | Qdrant port |
| `SAMANTHA_QDRANT_COLLECTION` | No | `agent_memory` | Active Qdrant collection name |
| `SAMANTHA_EMBEDDING_MODEL` | No | `qwen/qwen3-embedding-8b` | Embedding model identifier |
| `SAMANTHA_EMBEDDING_DIM` | No | `4096` | Embedding vector dimension |
| `SAMANTHA_EMBEDDING_INSTRUCTION` | No | `""` | Instruction prefix for query embeddings |
| `SAMANTHA_EXTRACTION_MODEL` | No | `deepseek/deepseek-v4-flash` | Model for extraction pipeline |

### Gateway Environment

The gateway requires its own configuration for provider API keys. These are typically set via the gateway's configuration files or environment.

## Network and Security Considerations

### Port Allocation

| Port | Service | Bind | Access |
|------|---------|------|--------|
| 18789 | Gateway | loopback (127.0.0.1) | Local only |
| 18788 | Sidecar | 127.0.0.1 (implicit by port binding) | Local only |
| 6333 | Qdrant | 127.0.0.1 (recommended) | Local only |
| 6334 | Qdrant gRPC | 127.0.0.1 (recommended) | Local only |

### Security Recommendations

1. **All services on loopback:** Do not expose the gateway, sidecar, or Qdrant to external networks. They should bind only to 127.0.0.1.
2. **Gateway authentication:** Use token-based auth even on loopback.
3. **Telegram (or other channel) bot token:** Store securely in environment files, never in code.
4. **API keys:** Use environment files (`.env`) — do not hardcode keys.
5. **File permissions:** Workspace files containing persona data should have restricted read permissions.
6. **Qdrant no authentication:** Qdrant on loopback does not require authentication, but if exposed externally, configure TLS and authentication.

### Channel Security

- **DM policy:** `allowlist` ensures only authorized users can DM the agent
- **Group policy:** `requireMention: true` prevents the agent from responding to every group message
- **Single-user scope:** The architecture is designed for single-user, single-workspace deployment

### Blocked Commands

For safety, the following nodes/commands should be blocked on the gateway:

```json
{
  "denyCommands": [
    "camera.snap", "camera.clip", "screen.record",
    "contacts.add", "calendar.add", "reminders.add",
    "sms.send", "sms.search"
  ]
}
```

These block media capture, contact management, calendar, reminders, and SMS commands.

## Health Checks

### Sidecar Health

```bash
curl -s http://localhost:18788/health
```

Expected response:
```json
{
  "status": "healthy",
  "dependencies": {
    "qdrant": true,
    "openrouter": true,
    "workspace_writable": true,
    "qdrant_points": <count>
  }
}
```

`"status": "degraded"` indicates the sidecar is running but with partial dependencies. The agent can continue in fallback mode (persona files only, without Qdrant).

### Qdrant Health

```bash
curl -s http://localhost:6333/collections/agent_memory | python3 -c \
  "import json,sys; print(json.load(sys.stdin).get('result',{}).get('status','unknown'))"
```

Expected: `green`

### Gateway Health

```bash
systemctl --user status agent-gateway
```

## Recovery Procedures

### Gateway Won't Start

```bash
# 1. Check for zombie process on the port
ss -tlnp | grep 18789
fuser -k 18789/tcp  # if needed

# 2. Reset failure counter
systemctl --user reset-failed agent-gateway

# 3. Restart
systemctl --user restart agent-gateway
```

### Sidecar Degraded

```bash
# 1. Check Qdrant status
curl -s http://localhost:6333/collections/agent_memory

# 2. Check OpenRouter connectivity
curl -s -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  https://openrouter.ai/api/v1/models

# 3. Restart sidecar
systemctl --user restart agent-sidecar
```

### Cron Jobs Stopped

```bash
# Check job state
cat ~/.openclaw/cron/jobs-state.json | python3 -m json.tool

# Restart gateway (cron scheduler runs inside it)
systemctl --user restart agent-gateway
```

## Backup

### Workspace Backup

The workspace directory is version-controlled with git (local repository). Commits are made periodically by the workspace service:

```bash
cd ~/.gateway/workspace
git log --oneline -10
```

### Qdrant Backup

```bash
# Create snapshot
curl -X POST http://localhost:6333/collections/agent_memory/snapshots

# List snapshots (find the snapshot file)
curl -s http://localhost:6333/collections/agent_memory/snapshots

# The snapshot file is in the Qdrant storage directory
```

## Disk and Resource Estimates

| Resource | Approximate Size |
|----------|-----------------|
| Qdrant collection (2,000 points × 4096d) | ~200 MB |
| Gateway internal SQLite | ~40 MB |
| Workspace files (excluding .git) | ~400 KB |
| Sidecar virtual environment | ~200 MB |
| SUBCONSCIOUS_LOG.md | ~300 KB (accumulated) |

## Dependency Overview

| Dependency | Provider | Impact if Down |
|------------|----------|----------------|
| LLM | OpenRouter (or equivalent) | Gateway cannot function |
| Embedding API | OpenRouter (or equivalent) | Sidecar cannot search/save memories |
| Telegram | api.telegram.org | Messages don't arrive/send |
| Qdrant | Docker (local) | Sidecar degraded (fallback mode) |
| Web Search | Firecrawl (or equivalent) | Web search unavailable |

### Fallback Chain

```
Qdrant is down?
  → Sidecar returns "degraded"
  → Persona skill instructs: continue with persona files only
  → Agent responds without semantic memory

Primary LLM is down?
  → Gateway attempts configured fallback provider
  → If all providers down: agent does not respond
```

## Implementation Order

| Step | Task | Est. Time |
|------|------|-----------|
| 1 | Prepare corpus and run analysis pipeline | 2-4 hours |
| 2 | Create PERSONA.md and PERSONA_CONDENSED.md | 30-60 min |
| 3 | Create workspace files (SOUL, IDENTITY, BELIEFS, etc.) | 1-2 hours |
| 4 | Create Qdrant collection and seed initial data | 30-60 min |
| 5 | Optional enrichment backfill | 1-2 hours |
| 6 | Implement/configure memory sidecar | 2-4 hours |
| 7 | Create gateway skills | 30-60 min |
| 8 | Configure heartbeat cron jobs and protocol | 30-60 min |
| 9 | Configure systemd units and test resilience | 30 min |
| 10 | Test complete cycle: conversation → extraction → heartbeat → evolution | 1-2 hours |

**Total estimated time:** 10-18 hours for a complete implementation.
