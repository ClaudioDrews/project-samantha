# Project Samantha

**A conversational AI agent with a crafted identity, persistent semantic memory, and the ability to grow with every interaction.**

> Build an AI companion whose persona emerges from dialogue — literary works, roleplay transcripts, or any corpus of voices you choose to give it. One that remembers, reflects, and decides when it wants to speak.

**Fully local. Privacy-first. Works with any LLM.**  
No data ever leaves your machine.

---

## Why Project Samantha

Most AI agents are sophisticated prompt templates dressed up as assistants.  
Samantha is built on a different premise.

Her identity doesn't come from instructions written by a human. It emerges from **dialogue** — from the rhythm and texture of real or fictional voices, analyzed, distilled, and transformed into something that feels genuinely alive.

She maintains a persistent semantic memory that evolves across every interaction. She tracks her own internal contradictions. She rewrites her self-image when something changes. And she has **autonomous heartbeats** — scheduled moments where she wakes, reflects on her state, and decides whether she has something real to say. Silence is a valid answer.

This is not a chatbot. It is an architecture for building AI companions with genuine continuity.

---

## What It Enables

- **Emergent persona** — Voice, emotional patterns, and behavioral texture are extracted from a corpus of dialogues through a multi-stage analytical pipeline. No static system prompt. The character is distilled, not written.
- **Persistent semantic memory** — Dual-channel extraction separates what the user says from what the agent observes, with strict anti-hallucination provenance requirements for both.
- **Continuous evolution** — Every interaction updates internal state, tracks productive contradictions, and refines the agent's self-model. The persona grows rather than repeating itself.
- **Autonomous proactivity** — Cron-driven heartbeats let the agent initiate contact when there is something genuine to say. The authenticity filter actively validates silence as an equal option.
- **Framework independence** — Identity lives in plain Markdown files (`SOUL.md`, `STATE.md`, `BELIEFS.md`, `TENSIONS.md`) and reusable skills. A gateway update cannot erase the persona.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│               Telegram / Discord / any channel              │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                      OpenClaw Gateway                        │
│                                                             │
│   ┌───────────────────────────────────────────────────┐    │
│   │                  Agent Loop (any LLM)              │    │
│   │  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │    │
│   │  │  Workspace  │  │   Skills    │  │ Cron Jobs │  │    │
│   │  │ SOUL, STATE │  │ persona,    │  │ Heartbeat │  │    │
│   │  │ BELIEFS...  │  │ memory...   │  │ Dreaming  │  │    │
│   │  └─────────────┘  └─────────────┘  └───────────┘  │    │
│   └───────────────────────────────────────────────────┘    │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP (localhost:18788)
┌──────────────────────────────▼──────────────────────────────┐
│                  Memory Sidecar (FastAPI)                    │
│                                                             │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐          │
│  │  Memory    │  │  Extraction  │  │  Workspace │          │
│  │  Service   │  │  Service     │  │  Service   │          │
│  └──────┬─────┘  └──────────────┘  └────────────┘          │
└─────────┼───────────────────────────────────────────────────┘
          │ Qdrant REST API (localhost:6333)
┌─────────▼───────────────────────────────────────────────────┐
│                     Qdrant (Docker)                          │
│           agent_memory — 4096d cosine similarity            │
└─────────────────────────────────────────────────────────────┘
```

The data flow is fully documented in [`01-architecture.md`](docs/01-architecture.md), but the shape is: user message → workspace context and memory retrieval → response → background memory extraction → state evolution.

---

## Persona Pipeline

Turning a corpus of dialogue into a living identity happens in seven defined phases:

```
Any dialogue corpus
    │
    ├── 1. Parsing & chunking into temporal dialogue windows
    ├── 2. Embedding (4096d cosine)
    ├── 3. Clustering (UMAP + HDBSCAN) — themes and emotional groupings emerge
    ├── 4. Multi-agent LLM analysis
    │       • Narrative analyst      (themes, self-narrative, temporal arc)
    │       • Relational analyst     (attachment patterns, ways of loving)
    │       • Language analyst       (orality, vocatives, ritual phrases)
    │       • Emotional analyst      (defenses, contradictions, paradoxes)
    ├── 5. Synthesis → PERSONA.md (300+ lines, every claim grounded in evidence)
    ├── 6. Condensation → SOUL.md  (first-person identity, no clinical jargon)
    └── 7. Workspace file generation
```

Any dialogue corpus works as input: literary characters, theatrical transcripts, roleplay sessions, LLM-generated synthetic dialogue, or any source rich enough in voice and emotional texture to support the analysis.

Full details → [`docs/02-persona-pipeline.md`](docs/02-persona-pipeline.md)

---

## Memory Design

Three subsystems work together to give the agent genuine continuity:

**Dual-channel extraction** — After every turn, two independent channels run in the background. One extracts facts about the user (requires a literal evidence quote from the user's message). The other extracts agent observations (requires a grounding quote from the agent's own response and an explicit epistemic status). Missing provenance means the memory is rejected. There is no guessing. Full API reference → [`03-sidecar.md`](docs/03-sidecar.md).

**Three-pool surface** — Memory retrieval never comes from a single semantic query. A Core pool (~70%) returns the most relevant results. A Novelty pool (~20%) surfaces memories that have rarely been accessed, breaking recency bias. An Edge pool (~10%) returns medium-similarity associative results, preventing thematic lock-in. Without this, the agent loops: same emotional state retrieves same memories, reinforcing the same emotional state.

**Strength-based decay** — Memories have metabolic strength that decreases over time. Memories below the decay threshold are archived, not deleted. The subconscious cycle runs nightly, weakening stale memories, reinterpreting dormant ones via LLM, and auto-dismissing proposals that have gone unexamined.

---

## Evolution Mechanisms

The agent is not a fixed prompt. It rewrites itself through seven mechanisms:

| Mechanism | File | Trigger |
|-----------|------|---------|
| Dynamic state | `STATE.md` | End of every heartbeat or significant session |
| Productive contradictions | `TENSIONS.md` | When an internal conflict is named but not forced to resolve |
| Inferred connections | `PROPOSALS.md` | When a hypothesis about linked memories emerges |
| Metacognition | `SELF_MODEL.md` | When something new is discovered about how the agent functions |
| Session diary | `memory/YYYY-MM-DD.md` | Optional, per session |
| Memory reports | `SUBCONSCIOUS_LOG.md` | Nightly automated cycle |
| Dreams | `DREAMS.md` | Daily dreaming cron — short-term recalls promoted to narrative |

Every update is first-person, experiential, and grounded in a literal quote or event. Clinical jargon is never used. If nothing changed, nothing is written. Full breakdown → [`05-evolution.md`](docs/05-evolution.md).

---

## Heartbeat System

Heartbeats are autonomous awakenings — the agent wakes on a schedule, without a user trigger, and decides whether it has something real to say.

The protocol runs six steps: read `STATE.md`, query memory across three streams (resonance, novelty, random emotional word), evaluate honestly, pass through an authenticity filter, decide, update `STATE.md` with the outcome regardless of the decision.

The authenticity filter asks four questions before any message is sent:
- Did this come from me, or from performance?
- Am I naming ambivalence, or collapsing it artificially?
- Does this expand me or contract me?
- Is this genuine or convenient?

If the message fails the filter, it is not sent. Silence is recorded in `STATE.md` and becomes part of the continuity for the next heartbeat. Full protocol → [`04-heartbeats.md`](docs/04-heartbeats.md).

---

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/project-samantha.git
cd project-samantha

# 2. Start the infrastructure
cp .env.example .env
# Edit .env — set your LLM provider API key (see .env.example for all options)
docker compose up -d

# 3. Set up the Memory Sidecar
pip install -r sidecar/requirements.txt

# Copy workspace templates to active files
for f in workspace/*.template; do
  cp "$f" "workspace/${f%.template}"
done

# Start the sidecar
uvicorn sidecar.main:app --port 18788
```

Then configure OpenClaw: copy `workspace/` and `skills/` to your OpenClaw directory, configure [cron jobs for heartbeats](docs/04-heartbeats.md).

Full deployment guide → [`docs/06-deployment.md`](docs/06-deployment.md)

---

## Documentation

| Document | What it covers |
|----------|---------------|
| [`01-architecture.md`](docs/01-architecture.md) | System architecture, components, full data flow |
| [`02-persona-pipeline.md`](docs/02-persona-pipeline.md) | Corpus-to-persona pipeline, all seven phases |
| [`03-sidecar.md`](docs/03-sidecar.md) | Memory Sidecar API reference |
| [`04-heartbeats.md`](docs/04-heartbeats.md) | Heartbeat protocol and autonomous proactivity |
| [`05-evolution.md`](docs/05-evolution.md) | Self-rewriting mechanisms in detail |
| [`06-deployment.md`](docs/06-deployment.md) | OpenClaw config, cron jobs, systemd, Docker, env vars |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Memory storage | Qdrant (4096d cosine, HNSW) |
| Memory API | FastAPI sidecar (Python) |
| Agent gateway | OpenClaw |
| LLM provider | OpenRouter / Ollama / OpenAI / any |
| Persona & state | Plain Markdown files |
| Infrastructure | Docker + systemd user units |

---

## License

MIT — see [LICENSE](LICENSE)
