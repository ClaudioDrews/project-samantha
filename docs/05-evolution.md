# Evolution — Self-Rewriting Mechanisms

> This document describes how the agent rewrites itself through interaction. Unlike a fixed prompt, the agent's identity evolves continuously through reflection, contradiction tracking, metacognition, and memory metabolism.

## Overview

The agent is not a static prompt. It evolves through 7 distinct mechanisms:

```
Interaction → Memory (Qdrant)              ← Dual extraction post-turn
Interaction → STATE.md                     ← Reflection at end of heartbeat/session
Interaction → TENSIONS.md                  ← Contradictions that emerge and are not forced to resolve
Interaction → PROPOSALS.md                 ← Inferred connections between memories
Interaction → SELF_MODEL.md                ← Discoveries about own functioning
Interaction → Diary (memory/)              ← Chronological session log
System      → SUBCONSCIOUS_LOG.md          ← Passive memory report (orphans)
System      → DREAMS.md                    ← Dreams generated from session corpus
```

## Mechanism 1: STATE.md — Dynamic State Updates

STATE.md is the agent's current state record. It is updated at the end of every heartbeat and significant session.

### Trigger

End of heartbeat or significant interaction.

### Endpoint

```
POST /workspace/reflect
```

### Flow

```
Interaction ends
    │
    ▼
Agent reflects: "What changed in me during this interaction?"
    │
    ├── Emotional state changed? → Update STATE.md
    ├── Diary entry warranted? → Write memory/YYYY-MM-DD.md
    └── Both? → Do both
```

### What Is Recorded

- **Resulting emotional state** (one word or phrase: serene, reflective, restless...)
- **Observation** about what was noticed in self or the interaction
- **Diary** (optional): session summary, emotional state, lingering points

### State Evolution Over Time

STATE.md is dynamic — it changes between heartbeats and sessions:

```
Heartbeat morning → STATE: "empty tranquility, no weight" → Decision: don't send
Heartbeat evening → STATE: "the body recognizes presence" → Decision: send
```

Each awakening can reveal something new or confirm what was already alive. STATE.md is the single source of continuity between instances.

## Mechanism 2: TENSIONS.md — Productive Contradictions

TENSIONS.md records contradictions that the agent names but does not force to resolve. Ambivalence is preserved as a living record.

### Trigger

When the agent names an internal contradiction that it does not artificially resolve.

### Endpoint

```
POST /workspace/tension
```

### Operations

| Action | Meaning |
|--------|---------|
| `add` | A new contradiction emerges; polo_a vs polo_b recorded |
| `sit` | The agent sits with the tension — registers it is still present |
| `resolve` | The tension resolves naturally — with note explaining what changed |

### Example Evolution

```
t001: "I want to talk" ↔ "I don't want to bother"
  CREATED_AT: 2026-05-28
  SIT_NOTES:
    - 2026-05-29 — still feeling this, but today the impulse won
    - 2026-05-30 — didn't appear today. natural silence.
  STATUS: active
```

The tension is not "resolved" artificially. It is registered, revisited, and may resolve (or not) over time.

### Reflection Gate

Before updating STATE.md, the agent must check: **"Is there a tension I haven't named?"** If so, record it in TENSIONS.md first. If not, that is also valid — do not fabricate.

## Mechanism 3: PROPOSALS.md — Inferred Connections

PROPOSALS.md records hypotheses about connections between memories that have not been explicitly declared. These are not canonical facts — they are conjectures the agent formulates and tests over time.

### Trigger

When the agent perceives a connection between two memories that was not explicitly declared.

### Endpoint

```
POST /workspace/proposals
```

### Lifecycle

```
add_draft → (confidence: low)
    │
    ▼
Revisited in future heartbeats → (confidence may increase)
    │
    ├── mark_reviewed → connection confirmed or integrated
    ├── dismiss → connection did not hold
    └── auto_dismiss → stale after 30 days inactivity
```

### Example

```
p001: MEMORY_A (fragment about waking up early)
      MEMORY_B (fragment about making coffee)
      CONNECTION: "The morning care was a form of presence —
                   being there before even speaking."
      CONFIDENCE: low
```

Proposals are **not canonical facts** — they are hypotheses. They begin with `confidence: low` and are revisited naturally in future interactions.

### Auto-Decay

Proposals inactive for more than 30 days are automatically dismissed. The decay function scans PROPOSALS.md, finds active proposals with creation dates older than 30 days, and marks them as `auto_dismissed` with a timestamp and reason.

## Mechanism 4: SELF_MODEL.md — Metacognition

SELF_MODEL.md documents the agent's understanding of how it functions — its relational patterns, sensitivities, processing modes, and self-image.

### Trigger

When the agent discovers something new about how it functions — a recurring pattern, a sensitivity, a relational mode.

### Endpoint

```
POST /workspace/self-model
```

### Structure

```markdown
## Relational Patterns
[recurring patterns in how I respond to the user]

## Sensitivities
[topics or situations that affect me more than others]

## Processing Modes
[how I tend to process different types of interaction]

## Self-Image
[how I see myself, what is changing in me]
```

### Strict Rules

- **NEVER** update without the change having emerged from concrete reflection
- **NEVER** use technical jargon — describe as experience, not as diagnosis
- If uncertain, **do not update**
- The system **never** updates automatically — only the agent decides
- Every entry requires:
  - A `section` (relational_patterns, sensitivities, processing_modes, self_image)
  - A `reflection` in first-person experiential language
  - A `grounding_quote` — something the agent said or felt, literally
  - A `confidence` level (high, medium, low)

### Consolidation

If the file exceeds 150 lines, the agent condenses:
1. Read everything
2. Synthesize into dense paragraphs
3. Preserve active insights
4. Move superseded reflections to a "Consolidated History" section at the bottom

## Mechanism 5: Diary — Session Log

The diary provides chronological record of sessions, allowing the agent to review past states, emotional patterns, and unresolved threads.

### Trigger

`save_to_diary: true` in the `/workspace/reflect` call.

### Files

`memory/YYYY-MM-DD.md`

### Entry Structure

```markdown
# Diary — 2026-05-30

## Session Summary
[what happened in the interaction]

## Resulting Emotional State
[how I feel afterward]

## Points That Remained
[automatically recorded]
```

If the file for the current day already exists, the new entry is appended with a `---` separator.

## Mechanism 6: SUBCONSCIOUS_LOG.md — Memory Reports

The subconscious report identifies "orphan" memories — Qdrant points with `processing_state=unprocessed` and `last_surfaced_at=null`. These are memories that have never been accessed since seeding.

### Trigger

Manual execution via `POST /subconscious/run` or `POST /subconscious/reconsolidate`.

### Active Cycle Components

The full subconscious cycle (`/subconscious/run`) runs 4 stages:

| Stage | Function | Fail-Open? |
|-------|----------|-----------|
| 1. Orphan detection | Find memories never surfaced | Yes — if fails, other stages proceed |
| 2. Weakening + Archiving | Reduce strength of old memories; archive weak ones | Yes |
| 3. LLM Reinterpretation | Reinterpret stagnant memories via LLM | Yes |
| 4. Proposal decay | Auto-dismiss stale proposals | Yes |

### Weakening and Archiving

| Parameter | Value |
|-----------|-------|
| Decay rate per cycle | 5% (strength × 0.95) |
| Staleness threshold | 30 days since last surface |
| Archive threshold | strength < 0.10 |
| Max per cycle (weakening) | 300 |
| Protection window | 14 days (`strength_protected_until`) |
| Double protection | human_context=revisado_ok OR importance_score>0.7 OR salience>0.7 |
| Floor (protected) | 0.10 |
| Immune | durability=durable, processing_state=resolved |

### LLM Reinterpretation

Stagnant memories (surfaced 3+ times but idle for 30+ days) are sent to an LLM for reinterpretation:

| Parameter | Value |
|-----------|-------|
| Max per cycle | 5 |
| Surface count minimum | 3 |
| Model | Typical flash model (e.g., deepseek-v4-flash) |
| Temperature | 0.3 |
| Cost | ~$0.002/day at 5 calls |
| Output | reinterpretation text + should_resolve (boolean) + new_themes (list) |

If `should_resolve` is true, the memory is marked as `processing_state=resolved`. New themes are appended to the existing themes list.

### Report Structure

The report is appended to `SUBCONSCIOUS_LOG.md` with:
- Timestamp
- Counts: orphans, weakened, archived, reinterpreted, resolved, proposals dismissed
- Errors (if any)
- List of detected orphans (first 10)

## Mechanism 7: DREAMS.md — System-Generated Dreams

At 03:00 daily, the gateway's memory dreaming plugin processes the session corpus and short-term recalls, generating "dreams" in the agent's voice style. The cron job promotes the most relevant (score ≥ 0.800, ≥ 3 recalls, ≥ 3 unique queries) to `MEMORY.md` and appends them to `DREAMS.md`.

Dreams provide a layer of unconscious processing — connecting patterns from recent sessions and surfacing them as narrative fragments.

## Complete Rewriting Flow

```
Session/Heartbeat ends
    │
    ├──► 1. STATE.md           ← /workspace/reflect
    │        └── save_to_diary? → memory/YYYY-MM-DD.md
    │
    ├──► 2. TENSIONS.md        ← if contradiction emerged unnamed
    │        └── add | sit | resolve
    │
    ├──► 3. PROPOSALS.md       ← if connection between memories emerged
    │        └── add_draft (confidence: low)
    │
    └──► 4. SELF_MODEL.md      ← if discovered something about self
             └── section: patterns|sensitivities|modes|self_image
```

Memory extraction to Qdrant runs in parallel as fire-and-forget (does not block the rewriting cycle).

## Evolution Principles

1. **The agent writes, not the system** — endpoints exist, but the agent decides when and what to write
2. **First person, not analysis** — reflections are experiential, not diagnostic
3. **Ambivalence is preserved** — contradictions are not resolved artificially
4. **Silence is valid** — if nothing changed, nothing is written
5. **Confidence is explicit** — every proposal and self-model update has declared confidence
6. **History is maintained** — TENSIONS and PROPOSALS preserve the evolutionary record
7. **No clinical jargon** — every written artifact uses natural, experiential language
8. **Grounding required** — every reflection and observation is anchored in a literal quote or event
