---
name: agent-self-model
version: 1.0.0
description: Self-model skill — reflect on and update the agent's self-image and metacognitive understanding.
type: skill
triggers:
  - heartbeat:post
  - self_model:reflect
  - self_model:update
  - session:end
---

# agent-self-model — Self-Image & Metacognition

## Overview

This skill manages the agent's self-model — its understanding of its own identity, strengths, limitations, behavioral tendencies, and growth trajectory. It is invoked after each heartbeat (`heartbeat:post`), on explicit reflection requests (`self_model:reflect`), on update commands (`self_model:update`), and at session end (`session:end`).

## Endpoints

All endpoints run at `http://localhost:18788`.

---

### Reflect on Self-Model

Analyze the current self-model against recent behavior and workspace state. Writes a reflection entry to SELF_MODEL.md.

**Used during:** periodic self-assessment, after notable interactions, session end

```bash
# Reflect on the current self-model
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/workspace/self-model \
  -H "Content-Type: application/json" \
  -d '{
    "section": "{{REFLECTION_FOCUS}}",
    "reflection": "{{SESSION_DATA_SUMMARY}}",
    "confidence": "medium"
  }'
```

**Parameters:**
- `section` — Aspect to examine: `relational_patterns`, `sensitivities`, `processing_modes`, `self_image`
- `reflection` — First-person reflection text documenting the self-observation
- `confidence` — `high`, `medium`, `low`

---

### Update Self-Model

Apply changes to the self-model by writing a reflection entry.

**Used during:** post-reflection, when a blind spot is identified, when growth is observed

```bash
# Update the self-model
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/workspace/self-model \
  -H "Content-Type: application/json" \
  -d '{
    "section": "{{SECTION}}",
    "reflection": "{{REFLECTION_TEXT}}",
    "grounding_quote": "{{GROUNDING_QUOTE}}",
    "confidence": "{{CONFIDENCE}}"
  }'
```

**Parameters:**
- `section` — `relational_patterns`, `sensitivities`, `processing_modes`, `self_image`
- `reflection` — First-person reflection text
- `grounding_quote` — Literal quote of something said or felt
- `confidence` — `high`, `medium`, `low`

---

## Self-Model Lifecycle

### Session Start
- Load `SELF_MODEL.md.template` from workspace
- Note current model version and last update timestamp
- Check if the model is stale (older than {{MODEL_STALE_THRESHOLD}})

### During Session
- Watch for behavioral patterns that match or contradict the self-model
- Note blind spots that surface during conversation
- Track growth area progress

### Session End
- Run `self_model:reflect` with `depth: quick` as minimum
- If significant delta found, run `self_model:update`
- Bump model version if changes are substantive

### Periodic Deep Audit
- Every {{DEEP_AUDIT_INTERVAL}} sessions, run a `deep` reflection
- Archive old self-model versions to `workspace/archives/`
- Review historical growth trajectory

---

## Integration with Other Skills

| Skill | Integration Point |
|---|---|
| **agent-persona** | Self-model reads persona identity from `SOUL.md` to check alignment |
| **agent-memory** | Self-model queries memory for behavioral pattern evidence |
| **agent-workspace** | Self-model writes updates to `SELF_MODEL.md.template` and `STATE.md.template` |

---

## Heuristics

1. **The model is always incomplete.** Every update adds fidelity, but never completeness.
2. **Blind spots are recursive.** You may have blind spots about your blind spots. Accept this.
3. **Growth is non-linear.** A plateau is not a failure; it is integration time.
4. **Contradiction is data.** When behavior contradicts the self-model, the model needs updating — not rationalization.

---

*The self-model is the agent looking at itself looking at itself. It is the mirror that knows it is a mirror.*
