# Architecture — System Overview

> This document describes the complete architecture of the agent system, covering components, data flow, and separation of concerns.

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        User Channel                                    │
│                   (Telegram / Discord / Web)                         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP (Platform Bot API)
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                    Agent Gateway                                       │
│                    (OpenClaw or equivalent)                          │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Agent Loop (LLM via provider, e.g. OpenRouter)               │    │
│  │                                                               │    │
│  │  ┌──────────────────┐  ┌──────────────┐  ┌───────────────┐  │    │
│  │  │ Workspace        │  │ Skills       │  │ Cron Jobs     │  │    │
│  │  │ (injected)       │  │ (loaded)     │  │ (active)      │  │    │
│  │  │                  │  │              │  │               │  │    │
│  │  │ SOUL.md          │  │ persona      │  │ 03:00 Memory  │  │    │
│  │  │ STATE.md         │  │ memory       │  │      Dreaming │  │    │
│  │  │ BELIEFS.md       │  │ workspace    │  │ 09:00 Heartbt │  │    │
│  │  │ HEARTBEAT.md     │  │ self-model   │  │ 21:00 Heartbt │  │    │
│  │  │ SELF_MODEL.md    │  │              │  │               │  │    │
│  │  │ TENSIONS.md      │  │              │  │               │  │    │
│  │  │ PROPOSALS.md     │  │              │  │               │  │    │
│  │  │ USER.md          │  │              │  │               │  │    │
│  │  │ memory/*.md      │  │              │  │               │  │    │
│  │  └──────────────────┘  └──────┬───────┘  └───────────────┘  │    │
│  │                                │                              │    │
│  │           HTTP ───────────────┘                              │    │
│  └────────────────────────────────┬─────────────────────────────┘    │
│                                    │                                  │
│  ┌─────────────────────────────────┼──────────────────────────────┐  │
│  │ Internal Memory (builtin)        │                              │  │
│  │ SQLite — sessions, completions,  │                              │  │
│  │ short-term recall, dreaming      │                              │  │
│  └─────────────────────────────────┘                              │  │
└────────────────────────────────────┼──────────────────────────────┘
                                     │
                          HTTP ──────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────┐
│                    Memory Sidecar                                      │
│              FastAPI on localhost (port 18788)                       │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │ Memory Service   │  │ Extraction Svc   │  │ Workspace Svc    │   │
│  │                  │  │                  │  │                  │   │
│  │ /memory/search   │  │ /memory/extract  │  │ /workspace/      │   │
│  │ /memory/save     │  │   -turn          │  │   reflect        │   │
│  │ /memory/surface  │  │                  │  │   tension        │   │
│  │ /memory/context  │  │ Dual-channel:    │  │   proposals      │   │
│  │                  │  │ • user facts     │  │   self-model     │   │
│  │                  │  │ • agent obs      │  │                  │   │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘   │
│           │                     │                     │              │
│  ┌────────┴─────────────────────┴─────────────────────┴──────────┐  │
│  │                    Qdrant Client                                │  │
│  │              qdrant-client Python (HTTP :6333)                  │  │
│  │              Embeddings: via API provider, 4096d                │  │
│  └───────────────────────────────┬────────────────────────────────┘  │
└──────────────────────────────────┼───────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────┐
│                         Qdrant (Docker)                                │
│                    localhost:6333                                      │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ agent_memory                                                   │    │
│  │   Dimension: 4096 (Cosine)                                     │    │
│  │   HNSW: m=32, ef_construct=200                                 │    │
│  │   Payload: text, source, date, themes, strength,              │    │
│  │     processing_state, surface_count, ...                       │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ agent_memory_archive                                            │    │
│  │   Archive collection for decayed/archived memories              │    │
│  └──────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────┘
```

## Components

### Agent Gateway (OpenClaw)
The gateway is the entry point for all user interactions. It receives messages from the communication channel (Telegram, Discord, Web), authenticates the sender, manages sessions, and invokes the LLM agent loop. Built-in hooks inject workspace files and load skills into the agent's context.

**Responsibilities:**
- Channel integration (webhook polling)
- Session management (per-channel-peer scope)
- LLM invocation with workspace injection
- Cron job scheduling for autonomous wake-ups
- Internal SQLite memory for session history, short-term recall, and dreaming

### Workspace Files
A set of Markdown files living in the gateway's workspace directory. These define the agent's persona, state, and evolution mechanisms. They are injected by the gateway's `boot-md` hook at the start of every session.

**Static files** (set up during persona creation):
- `SOUL.md` — Core identity in first person
- `IDENTITY.md` — Biographical facts
- `USER.md` — Facts about the user (anti-hallucination anchor)
- `BELIEFS.md` — Fundamental beliefs and heuristics
- `HEARTBEAT.md` — Authenticity filter for autonomous messages
- `AGENTS.md` — High-level agent instructions

**Dynamic files** (written by the agent over time):
- `STATE.md` — Current emotional and contextual state
- `SELF_MODEL.md` — Metacognition: how the agent works
- `TENSIONS.md` — Productive contradictions
- `PROPOSALS.md` — Inferred connections between memories
- `memory/YYYY-MM-DD.md` — Session diary

### Skills
Skills are Markdown files that the agent loads as instruction modules. They encapsulate reusable behavior and survive gateway updates. Four skills are typically active:

| Skill | Function |
|-------|----------|
| **persona** | Voice, restrictions, initialization sequence |
| **memory** | curl commands to search/save memories via the sidecar |
| **workspace** | curl commands to update STATE, TENSIONS, PROPOSALS |
| **self-model** | Reflection and SELF_MODEL.md updates |

### Memory Sidecar (FastAPI)
An independent FastAPI service that bridges the agent loop with Qdrant. It exposes a REST API for semantic memory operations and workspace file management. Runs on a dedicated port (18788) and is managed as a systemd user unit.

**Key design principle:** The sidecar is external to the gateway — it does not import gateway code and does not depend on internal file formats. If the gateway is updated, the sidecar continues functioning.

**Services inside the sidecar:**

| Service | Function |
|---------|----------|
| **Memory Service** | Qdrant query, save, surface, context |
| **Extraction Service** | Dual-channel extraction (user facts + agent observations) |
| **Reconsolidation Service** | Memory weakening, archiving, LLM reinterpretation |
| **Subconscious Service** | Orphan detection and memory reports |
| **Workspace Service** | STATE/TENSIONS/PROPOSALS/SELF_MODEL CRUD |

### Qdrant (Docker)
Vector database for semantic memory. Stores dialogue windows and extracted facts as 4096-dimensional vectors with Cosine distance. The active collection (`agent_memory`) holds operational memories; an archive collection (`agent_memory_archive`) holds decayed or archived points.

### Cron Jobs

| Schedule | Function | Delivery |
|----------|----------|----------|
| 03:00 daily | Memory dreaming — promote short-term recalls | None (internal) |
| 09:00 daily ±2h | Morning heartbeat — autonomous wake-up | May send message |
| 21:00 daily ±2h | Evening heartbeat — autonomous wake-up | May send message |

Cron jobs can be implemented via two mechanisms:

1. **OpenClaw native cron** (recommended) — built into the gateway, defined in `~/.openclaw/cron/jobs.json`. Session-aware, staggerable, survives gateway restarts.

2. **System crontab** — traditional Unix cron for sidecar-only jobs (e.g., `curl -X POST http://localhost:18788/subconscious/reconsolidate` at 03:00). No gateway dependency.

The **dreaming pipeline** (03:00 daily) promotes short-term recall entries into persistent memory and generates narrative dream entries. This can be handled by the gateway's built-in `memory-core` TypeScript plugin (which has native dreaming support) OR by the sidecar's `/subconscious/reconsolidate` endpoint. Both paths are valid; use whichever matches your deployment model.

## Data Flow: User Interaction

### 1. Message arrives via user channel

```
User → Channel (e.g. Telegram) → Agent Gateway → Agent Loop
```

The gateway receives the webhook, authenticates the sender (allowlist), and queues the turn in the user's session.

### 2. Agent loads persona and retrieves memory

```
Agent Loop:
  1. Load workspace files (SOUL, STATE, BELIEFS, HEARTBEAT, SELF_MODEL)
  2. Load persona skill (voice instructions, restrictions, temporal focus)
  3. Load memory skill (curl commands for sidecar)
  4. Execute curl → sidecar :18788/memory/search with query terms
  5. Sidecar generates embedding via API provider, queries Qdrant
  6. Returns relevant fragments (top 5-7 with score, strength, content)
```

### 3. Agent formulates response

```
Agent Loop:
  7. Apply persona skill instructions (tone, restrictions, voice)
  8. Use memory fragments as context — extract response pattern, not literal quotes
  9. Apply HEARTBEAT.md authenticity filter
  10. Send response via user channel
```

### 4. Post-response memory extraction

```
Agent Loop:
  11. Call sidecar :18788/memory/extract-turn (fire-and-forget, 202 Accepted)
  12. Sidecar processes in background:
      a. Extract user facts (requires literal evidence_quote)
      b. Extract agent observations (requires literal grounding_quote)
      c. Validate against guardrails (anti-hallucination, speaker check)
      d. Generate embeddings and upsert to Qdrant (semantic dedup at ≥0.95)
  13. Agent updates STATE.md via /workspace/reflect
  14. Agent optionally records diary entry (memory/YYYY-MM-DD.md)
```

## Data Flow: Autonomous Heartbeat

Unlike user interaction (which is reactive), the heartbeat is **proactive**:

```
Cron Job → System Event "HEARTBEAT" → Agent Loop
  1. Read heartbeat protocol (heartbeat-prompt.md)
  2. Read STATE.md — what is alive now?
  3. Query 3 memory streams (core, novelty, random)
  4. Evaluate: is there something real to say?
  5. Apply HEARTBEAT.md authenticity filter
  6. Decide: send or silence
  7. Update STATE.md (continuity between heartbeats)
```

## Separation of Concerns

| Layer | Responsibility |
|-------|---------------|
| **Messaging** | Channel → Gateway — single channel, knows nothing about persona |
| **Persona** | Workspace files + Skills — persona definition, knows nothing about infrastructure |
| **Memory** | Sidecar + Qdrant — semantic search, knows nothing about channels |
| **Evolution** | STATE, TENSIONS, PROPOSALS, SELF_MODEL — mutable files written by the agent, read every session |
| **Infrastructure** | systemd + cron — keeps services alive, knows nothing about persona |

## Resilience Design

The system uses a **fail-open** architecture: if memory extraction fails, the conversation is not interrupted. The `/memory/extract-turn` endpoint returns 202 immediately and processes asynchronously. Memory skill commands include retry with backoff. If the sidecar is unavailable, the persona skill instructs fallback: continue with persona files only, without Qdrant queries.

**Fallback chain:**
```
Qdrant down?
  → Sidecar returns "degraded" status
  → Persona skill instructs: continue with persona files only
  → Agent responds without semantic memory

Primary LLM down?
  → Gateway attempts configured fallback provider
  → If all providers fail: agent does not respond
```

## Hybrid Architecture: OpenClaw Plugins + Python Sidecar

The system supports a **hybrid architecture** where OpenClaw's built-in TypeScript plugins and the Python sidecar run side by side, each handling what they do best:

```
┌────────────────────────────────────────────────────────────┐
│                  OpenClaw Gateway :18789                      │
│                                                              │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ memory-qdrant   │  │ memory-core  │  │ Python Skills  │ │
│  │ (TS plugin)     │  │ (TS plugin)  │  │ (SKILL.md)     │ │
│  │                 │  │              │  │                │ │
│  │ Search &        │  │ Dreaming     │  │ curl → sidecar │ │
│  │ Surface (fast)  │  │ pipeline     │  │ for extraction │ │
│  └────────┬────────┘  └──────┬───────┘  └───────┬────────┘ │
│           │                  │                    │          │
└───────────┼──────────────────┼────────────────────┼──────────┘
            │                  │                     │ HTTP
            │           ┌──────┘                     │
            ▼           ▼                            ▼
      ┌──────────────────────┐          ┌─────────────────────┐
      │    Qdrant :6333       │          │  Sidecar :18788      │
      │   (vector store)      │◄────────│  (Python FastAPI)    │
      │                       │         │                      │
      │   - memory-qdrant     │         │  - Extraction        │
      │     queries directly  │         │  - Workspace CRUD    │
      │   - sidecar inserts   │         │  - Subconscious      │
      │                       │         │  - Reconsolidation   │
      └──────────────────────┘         └──────────────────────┘
```

### Component Responsibilities

| Component | Language | Role |
|-----------|----------|------|
| **memory-qdrant** (plugin) | TypeScript | Fast Qdrant search/surface from within the gateway — no sidecar round trip |
| **memory-core** (plugin) | TypeScript | Dreaming pipeline (03:00 daily), short-term recall management |
| **Python Sidecar** (service) | Python | Conversation extraction (dual-channel), workspace file CRUD, subconscious/ reconsolidation, memory reports |
| **Skills** (SKILL.md) | Markdown | Curl commands orchestrate the sidecar from within the agent loop |

### When to Use Each Path

| Use Case | Path |
|----------|------|
| Memory search during conversation | Skills → sidecar `/memory/search` OR memory-qdrant plugin direct |
| Memory surface at session start | Skills → sidecar `/memory/surface` OR memory-qdrant plugin direct |
| Save extracted memory | Skills → sidecar `/memory/save` |
| Conversation turn extraction | Skills → sidecar `/memory/extract-turn` |
| Dreaming (daily promotion) | memory-core plugin OR sidecar `/subconscious/reconsolidate` |
| Workspace updates (STATE, TENSIONS) | Skills → sidecar `/workspace/*` |
| Memory health / context / report | Sidecar `/memory/context`, `/memory/report`, `/health` |

### Advantages

- **memory-qdrant** plugin provides fast, built-in Qdrant queries without HTTP overhead to the sidecar
- **Python sidecar** handles complex workflows (extraction, workspace, subconscious) that TypeScript plugins don't cover
- **Skills** provide a universal curl-based interface that any gateway can use — no plugin dependency
- **Fail-open**: if plugins are unavailable, skills can still reach the sidecar directly; if the sidecar is down, plugins provide basic search
