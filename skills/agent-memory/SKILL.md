---
name: agent-memory
version: 1.0.0
description: Memory operations skill — curl commands to localhost:18788 endpoints for search, save, surface, and extract-turn operations.
type: skill
triggers:
  - heartbeat:step2
  - memory:search
  - memory:save
  - memory:surface
  - memory:extract
---

# agent-memory — Memory Operations

## Overview

This skill provides curl-based access to the Memory API running on `localhost:18788`. It handles all memory operations: searching stored memories, saving new observations, surfacing relevant context at session start, and extracting key information from conversation turns.

## Endpoints

All endpoints run at `http://localhost:18788`.

---

### Search Memories

Semantic search across stored memories. Supports filtering by recency, relevance, and resolution status.

**Used during:** heartbeat Step 2 (3 currents: recent, relevant, unresolved), on-demand user queries

```bash
# Search all memories by query
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/memory/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "{{QUERY_TEXT}}",
    "limit": {{LIMIT}},
    "current": "{{CURRENT}}"
  }'
```

**Parameters:**
- `query` — Semantic search text
- `limit` — Max results (default: 5)
- `current` — One of: `recent`, `relevant`, `unresolved`

---

### Save Memory

Store a new observation or reflection in the memory store.

**Used during:** post-response, significant exchange detection, session end

```bash
# Save a memory
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/memory/save \
  -H "Content-Type: application/json" \
  -d '{
    "content": "{{MEMORY_CONTENT}}",
    "tags": {{TAGS}},
    "source": "{{SOURCE_CONTEXT}}",
    "timestamp": "{{TIMESTAMP}}"
  }'
```

**Parameters:**
- `content` — The memory text to store
- `tags` — Array of classification tags (e.g., `["user:preference", "topic:philosophy"]`)
- `source` — Context where the memory was generated (e.g., `session:2026-05-31`)

---

### Context Probe

Get memory statistics and collection information. Useful for diagnostics and monitoring.

**Used during:** diagnostics, startup health check

```bash
# Get memory context statistics
curl -s --retry 3 --retry-delay 2 -X GET http://localhost:18788/memory/context
```

---

### Surface Memories

Load relevant memories at session start. Returns the most important memories for the current context.

**Used during:** `session_start`, `workspace:load`

```bash
# Surface memories for session context
curl -s --retry 3 --retry-delay 2 -X GET 'http://localhost:18788/memory/surface?query={{SESSION_CONTEXT}}&limit={{LIMIT}}'
```

---

### Extract Turn

Extract key information from a conversation turn and optionally save it.

**Used during:** post-response processing, reflection cycles

```bash
# Extract key data from a turn
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/memory/extract-turn \
  -H "Content-Type: application/json" \
  -d '{
    "turn": "{{TURN_TEXT}}",
    "auto_save": {{AUTO_SAVE}}
  }'
```

---

## Memory Currents

The system categorizes memories into three currents:

| Current | Purpose | Search signal |
|---|---|---|
| **Recent** | Last N interactions | `current: "recent"`, sorted by timestamp desc |
| **Relevant** | Topically related | `current: "relevant"`, sorted by semantic similarity |
| **Unresolved** | Open threads, pending items | `current: "unresolved"`, filtered by `status: open` |

## Usage Notes

- Always include timestamps in ISO 8601 format when saving
- Tag memories with at least one category tag for better retrieval
- Keep memory content concise — no more than {{MEMORY_MAX_CHARS}} characters

---

*Memory is the agent's continuity. Without it, every session is a first meeting.*
