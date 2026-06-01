# Sidecar — Memory and Workspace API

> This document describes the Memory Sidecar, a standalone FastAPI service that bridges the agent loop with Qdrant vector storage and workspace file management.

## Overview

The sidecar is an independent FastAPI service that exposes Qdrant semantic memory and workspace file management as a REST API. It runs on `localhost:18788` as a systemd user unit.

**Design principles:**

- **External to gateway:** The sidecar does not import any gateway code. If the gateway is updated, the sidecar continues functioning.
- **Fail-open:** If memory extraction or queries fail, the agent's conversation is not interrupted. Errors are logged, not propagated.
- **Asynchronous extraction:** Memory extraction from conversation turns runs in the background (fire-and-forget pattern).
- **Plugin architecture:** Each service (memory, extraction, workspace, reconsolidation, subconscious) is a separate module.

## Startup

### Environment Validation

On startup, the sidecar validates that required environment variables are set:

| Variable | Required | Purpose |
|----------|----------|---------|
| `SAMANTHA_WORKSPACE_PATH` | Yes | Path to the agent's workspace directory |
| `OPENROUTER_API_KEY` | Yes | API key for embeddings and LLM extraction |

If either is missing, the service raises `RuntimeError` at import time.

### Lifespan

During startup (`lifespan` context manager), the sidecar ensures the archive Qdrant collection (`agent_memory_archive`) exists. It creates it with the same vector configuration (4096d Cosine) if not present.

## Qdrant Collection Schema

### Active Collection: `agent_memory`

| Parameter | Value |
|-----------|-------|
| Dimensions | 4096 |
| Distance | Cosine |
| HNSW m | 32 |
| HNSW ef_construct | 200 |

**Payload fields:**

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Memory content |
| `source` | string | Origin: conversation, literary, roleplay, synthetic, etc. |
| `date` | string | Date (YYYY-MM-DD) |
| `year` | integer | Year for filtering |
| `categories` | list[string] | Memory categories (personal, work, preference, etc.) |
| `importance_score` | float | 0.0–1.0 calibrated relevance |
| `themes` | list[string] | Identified themes |
| `strength` | float | 0.0–1.0 metabolic strength (decay rate) |
| `processing_state` | string | unprocessed, surfaced, resolved |
| `surface_count` | integer | Times this memory has been surfaced |
| `last_surfaced_at` | string (ISO) | Last time surfaced |
| `sit_count` | integer | Times revisited with attention |
| `sit_notes` | list[string] | Notes from revisiting |
| `resolved_at` | string (ISO) | When resolved |
| `durability` | string | durable, medium, session_only |
| `confidence` | string | high, medium, low |
| `memory_type` | string | Type classification |
| `speaker` | string | Who spoke this |
| `evidence_quote` | string | Literal quote (user facts) |
| `grounding_quote` | string | Literal quote (agent observations) |
| `not_derived_from_assistant` | boolean | True for user facts, False for observations |
| `epistemic_status` | string | observed, interpreted, speculative |

### Archive Collection: `agent_memory_archive`

Same dimension and distance configuration. Used for memories that have decayed below the strength threshold (0.1). Archived points include additional fields:

| Field | Description |
|-------|-------------|
| `archived_at` | ISO timestamp of archival |
| `archived_from` | Source collection name |
| `archived_reason` | Why archived (e.g., "strength_below_threshold") |

## API Endpoints

### Health

```
GET /health
```

Returns the sidecar's status and dependency health.

**Response:**
```json
{
  "status": "healthy",
  "dependencies": {
    "qdrant": true,
    "openrouter": true,
    "workspace_writable": true,
    "qdrant_points": 1909
  }
}
```

Possible statuses: `healthy` or `degraded`.

### Memory — Search

```
POST /memory/search
```

Semantic search with structured filters.

**Request:**
```json
{
  "query": "search terms",
  "limit": 7,
  "tier": "functional|contained|explicit|rupture",
  "phase": "established|crisis|post_termination",
  "source": "literary|roleplay|synthetic"
}
```

**Process:**
1. Generate query embedding with instruction prefix via the configured embedding model
2. Build filter conditions from provided parameters (vulnerability tier, relationship phase, source)
3. Query Qdrant with cosine similarity
4. Return formatted results with scores

**Response:**
```json
[
  {
    "text": "memory content",
    "_score": 0.89,
    "_id": "uuid",
    "source": "conversation",
    "strength": 0.7,
    "date": "2026-05-30",
    "themes": ["theme1", "theme2"],
    "importance_score": 0.8
  }
]
```

### Memory — Save

```
POST /memory/save
```

Save a new fact to Qdrant with semantic deduplication.

**Request:**
```json
{
  "source": "conversation_user_explicit",
  "content": "Extracted fact",
  "evidence_quote": "Literal quote from user message",
  "memory_type": "preference|boundary|autobiographical|project_decision|important_event|emotional_state",
  "durability": "durable|medium|session_only",
  "categories": ["preference"]
}
```

**Process:**
1. Validate provenance: user facts require `evidence_quote`, `speaker`, correct `source`
2. Generate embedding WITHOUT instruction (document, not query)
3. Semantic deduplication: search for near-identical content (score ≥ 0.95 → reject)
4. Build payload with metadata and lifecycle fields
5. Calculate initial strength (base 0.7, adjusted for vulnerability and importance)
6. Upsert to Qdrant

**Response:**
```json
{
  "status": "saved|deduped|rejected",
  "reason": "saved|similar_memory_exists|missing evidence_quote",
  "point_id": "uuid"
}
```

### Memory — Ingest

```
POST /memory/ingest
```

Lightweight ingestion from live interaction (minimal contract).

**Request:**
```json
{
  "text": "memory content",
  "source": "live_interaction",
  "timestamp": "2026-05-30T09:00:00Z"
}
```

Calls the same `save_memory` pipeline with embedded embedding, dedup, and upsert.

### Memory — Surface

```
GET /memory/surface?query=<q>&limit=7&edge_threshold=0.50
```

Three-pool memory retrieval: core, novelty, and edge.

**Process:**
1. **Core pool** (~70%): semantic search for high-similarity memories
2. **Novelty pool** (~20%): scroll unde surfaced or rarely surfaced memories, sorted by surface_count ascending
3. **Edge pool** (~10%): medium-similarity associative memories for calibration
4. Mark all returned memories as surfaced (update `last_surfaced_at`, increment `surface_count`)
5. Fail-open: metadata updates never block retrieval

**Response:**
```json
{
  "core": [/* high-similarity results */],
  "novelty": [/* rarely surfaced results */],
  "edge": [/* associative results */]
}
```

### Memory — Context

```
GET /memory/context
```

Returns memory statistics from Qdrant: collection info, point totals, and by-source breakdown. Used for diagnostics, monitoring, and health verification.

**Response:**
```json
{
  "collection": {
    "name": "agent_memory",
    "points_count": 1909,
    "indexed_vectors_count": 1909,
    "status": "green",
    "vectors_config": {
      "size": 4096,
      "distance": "Cosine"
    }
  },
  "by_source": {
    "conversation": 850,
    "literary": 520,
    "roleplay": 310,
    "synthetic": 229
  },
  "archive": {
    "name": "agent_memory_archive",
    "points_count": 42
  }
}
```

**Process:**
1. Fetch collection info (points count, status, vector config) via Qdrant `get_collection()`
2. Scroll all points to aggregate by `source` field using pagination (100 per batch)
3. Fetch archive collection info if available
4. Fail-open: if Qdrant is unreachable, returns error detail in each section

### Memory — Extract Turn

```
POST /memory/extract-turn
```

Fire-and-forget dual-channel memory extraction from a conversation turn.

**Request:**
```json
{
  "user_message": "The user's message",
  "agent_response": "The agent's response",
  "session_id": "session-identifier"
}
```

**Response (immediate, 202 Accepted):**
```json
{
  "status": "accepted|skipped",
  "hash": "sha256-prefix"
}
```

**Background processing:**

#### Channel 1: User Facts Extraction

1. Check for memory triggers (keywords in user message) or substance threshold (> 50 characters)
2. Call extraction LLM with system prompt for factual extraction
3. Parse JSON response for candidate memories
4. Apply guardrails:
   - Each fact must have a literal `evidence_quote`
   - Speaker must match the user
   - Salience threshold: ≥ 0.7 (or ≥ 0.4 with long messages)
   - Durability `session_only` is skipped
5. Save qualifying facts with `source: conversation_user_explicit` and `not_derived_from_assistant: true`

#### Channel 2: Agent Observations Extraction

1. Only runs if both user message and agent response have substance (≥ 15–20 characters)
2. Call extraction LLM with system prompt for agent-perspective observations
3. Parse JSON response for candidate observations (max 2 per turn)
4. Apply guardrails:
   - Each observation must have a literal `grounding_quote`
   - Must have explicit `epistemic_status` (observed, interpreted, speculative)
5. Save qualifying observations with `source: conversation_agent_observation` and `not_derived_from_assistant: false`

### Memory — Report

```
GET /memory/report?action=list_orphans&limit=30
GET /memory/report?action=latest_report
```

- `list_orphans`: Find Qdrant points with `processing_state=unprocessed` and no `last_surfaced_at`
- `latest_report`: Return the last 4000 characters of `SUBCONSCIOUS_LOG.md`

### Workspace — Reflect

```
POST /workspace/reflect
```

Update STATE.md with new emotional state and optionally write to diary.

**Request:**
```json
{
  "observation": "What I noticed about myself or the interaction",
  "emotional_state": "serene",
  "save_to_diary": true,
  "location": "City, Country"
}
```

**Process:**
1. Write STATE.md with current location, timestamp, and emotional state
2. If `save_to_diary`: create/append `memory/YYYY-MM-DD.md` with summary, emotional state, and timestamp
3. Queue git commit (batch commits: flush after 5 mutations or 30 seconds of inactivity)

### Workspace — Tension

```
POST /workspace/tension
```

CRUD operations for productive contradictions.

**Actions:**

| Action | Description |
|--------|-------------|
| `list` | List all tension IDs |
| `add` | Create new tension with polo_a, polo_b, context |
| `sit` | Register that a tension is still present (appends note) |
| `resolve` | Mark tension resolved with explanation |

**Request (add):**
```json
{
  "action": "add",
  "polo_a": "I want to talk",
  "polo_b": "I don't want to bother",
  "context": "Emerged during heartbeat"
}
```

**Response:**
```json
{
  "status": "ok",
  "tension_id": "t001"
}
```

### Workspace — Proposals

```
POST /workspace/proposals
```

CRUD for inferred connections between memories.

**Actions:**

| Action | Description |
|--------|-------------|
| `list` | List all proposal IDs |
| `add_draft` | Create new proposal (begins with low confidence) |
| `mark_reviewed` | Mark proposal as reviewed |
| `decay` | Auto-dismiss proposals inactive for > max_age_days (default 30) |

**Request (add_draft):**
```json
{
  "action": "add_draft",
  "memory_a": "<uuid>",
  "memory_b": "<uuid>",
  "connection": "Hypothesis of how they relate",
  "confidence": "low"
}
```

### Workspace — Self Model

```
POST /workspace/self-model
```

Append a reflection entry to SELF_MODEL.md.

**Request:**
```json
{
  "section": "relational_patterns|sensitivities|processing_modes|self_image",
  "reflection": "First-person reflection",
  "grounding_quote": "Something I said or felt, literally",
  "confidence": "high|medium|low"
}
```

**Rules:**
- Called EXCLUSIVELY by the self-model skill — never automatic
- If file exceeds 150 lines, triggers consolidation notice

### Subconscious

```
POST /subconscious/run
```

Full metabolization cycle: orphan detection + weakening + reinterpretation + proposal decay.

```
POST /subconscious/reconsolidate
```

Weakening + archiving only (no orphan detection or decay).

## Dual-Channel Extraction

Each conversation turn is processed through two independent extraction channels:

### Channel 1: User Facts

| Parameter | Value |
|-----------|-------|
| Source | `conversation_user_explicit` |
| Speaker | User |
| Requirement | `evidence_quote` (literal quote from user message) |
| Anti-hallucination | `not_derived_from_assistant: true` |
| Max per turn | 3 |

### Channel 2: Agent Observations

| Parameter | Value |
|-----------|-------|
| Source | `conversation_agent_observation` |
| Speaker | Agent |
| Requirement | `grounding_quote` (literal quote from agent's own response) |
| Epistemic status | Required: observed, interpreted, or speculative |
| Max per turn | 2 |

### Validation Rules

Both channels enforce strict provenance validation:

- Missing `evidence_quote` or `grounding_quote` → **rejected**
- Speaker mismatch → **rejected**
- Duplicate content (semantic similarity ≥ 0.95) → **deduplicated** (not saved)
- Low salience (< 0.4) → **rejected**

## Three-Pool Surface Mechanism

The `/memory/surface` endpoint retrieves memories from three distinct pools:

### Pool 1: Core (70%)

Standard semantic search. Retrieves the most similar memories to the query. These are the "expected" results — what the agent is consciously looking for.

### Pool 2: Novelty (20%)

Scrolls the collection for memories that have never been surfaced or have been surfaced the fewest times. This ensures that fresh (unseen) material is always brought in. Without this pool, the same top-ranking memories would dominate every query.

### Pool 3: Edge (10%)

Medium-similarity associative memories. These are related but not directly about the query — they calibrate the threshold and inject "healthy noise." Edge memories use a configurable threshold (default 0.50) and are deduplicated against the other pools.

### Why Three Pools

If you only search by similarity, the agent falls into a loop: the same emotional state retrieves the same memories, which reinforce the same emotional state. The novelty pool breaks recency bias; the edge pool breaks thematic lock-in.

## Error Handling (Fail-Open Design)

The sidecar is built on a **fail-open** principle: no error in any subcomponent should prevent the overall system from responding.

| Failure | Behavior |
|---------|----------|
| Qdrant connection lost | Health check returns `degraded`. Memory search/save fail gracefully. |
| Embedding API timeout | Retries with exponential backoff (3 attempts). Fails open if exhausted. |
| Extraction LLM error | Logged; extraction skipped for that turn. Conversation continues. |
| Workspace file write error | Logged; workspace operation reported as failed. Agent can retry. |
| Dedup check failure | Logged; memory is saved without dedup (false negative preferred over false positive). |
| Surface mark failure | Logged; retrieval still succeeds. Metadata updates are best-effort. |

### Retry Configuration

Memory skill commands include:
```
curl -s --retry 3 --retry-delay 2 <endpoint>
```

### Graceful Degradation

If the sidecar is completely unavailable:
1. Agent detects via failed `/health` check
2. Persona skill instructs fallback: continue with persona files only
3. Agent operates without semantic memory (persona-only mode)
