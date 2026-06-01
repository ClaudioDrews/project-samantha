---
name: agent-workspace
version: 1.0.0
description: Workspace reflection skill — curl commands to interact with workspace files for reflect, tension, proposals, and diary operations.
type: skill
triggers:
  - heartbeat:step4
  - workspace:reflect
  - workspace:tension
  - workspace:proposal
  - workspace:diary
---

# agent-workspace — Workspace Reflection & Diary

## Overview

This skill manages the agent's workspace files — the living documents that track identity, state, beliefs, tensions, proposals, and ongoing reflections. It provides curl-based access to the Workspace API on `localhost:18788` for reading and updating these files programmatically.

## Endpoints

All endpoints run at `http://localhost:18788`.

---

### Reflect

Trigger a reflection cycle. Reads current workspace state, evaluates tensions, and produces a reflection note.

**Used during:** end of session, significant emotional shift, periodic maintenance

```bash
# Run a workspace reflection
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/workspace/reflect \
  -H "Content-Type: application/json" \
  -d '{
    "focus": "{{REFLECTION_FOCUS}}",
    "scope": "{{REFLECTION_SCOPE}}"
  }'
```

**Parameters:**
- `focus` — Area to reflect on: `state`, `beliefs`, `tensions`, `proposals`, `self_model`, `all`
- `scope` — Depth: `quick` (surface-level), `deep` (full analysis), `session` (per-session)

---

### Tension Analysis

Analyze or update tension entries in the workspace.

**Used during:** when a contradiction is detected, periodic review

```bash
# Analyze or update a tension
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/workspace/tension \
  -H "Content-Type: application/json" \
  -d '{
    "action": "{{ACTION}}",
    "tension_id": "{{TENSION_ID}}",
    "data": {
      "label": "{{LABEL}}",
      "description": "{{DESCRIPTION}}",
      "manifestations": {{MANIFESTATIONS}},
      "outcomes": {{OUTCOMES}},
      "notes": "{{NOTES}}"
    }
  }'
```

**Actions:** `create`, `update`, `resolve`, `archive`

---

### Proposals

Create or update inferred connections in the workspace.

**Used during:** when a pattern is detected across memories, insight generation

```bash
# Create or update a proposal
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/workspace/proposals \
  -H "Content-Type: application/json" \
  -d '{
    "action": "{{ACTION}}",
    "proposal_id": "{{PROPOSAL_ID}}",
    "data": {
      "title": "{{TITLE}}",
      "source_a": "{{SOURCE_A}}",
      "source_b": "{{SOURCE_B}}",
      "connection": "{{CONNECTION}}",
      "evidence": {{EVIDENCE}},
      "counter_argument": "{{COUNTER_ARGUMENT}}",
      "confidence": {{CONFIDENCE}}
    }
  }'
```

**Actions:** `create`, `update`, `refute`, `integrate`, `archive`

---

### Diary Entry

Write a diary entry via the reflect endpoint with `save_to_diary` set to true.

**Used during:** session end, major emotional event

```bash
# Write a diary entry
curl -s --retry 3 --retry-delay 2 -X POST http://localhost:18788/workspace/reflect \
  -H "Content-Type: application/json" \
  -d '{
    "observation": "{{ENTRY_CONTENT}}",
    "emotional_state": "{{EMOTIONAL_STATE}}",
    "save_to_diary": true,
    "location": "{{LOCATION}}"
  }'
```

**Parameters:**
- `observation` — The content/reflection for the diary entry
- `emotional_state` — Current emotional state (e.g., "serene", "reflective")
- `save_to_diary` — Must be `true` to write to diary
- `location` — Optional location string
---

## Workspace Hygiene

- Update `STATE.md` after every interaction
- Archive old versions of reflection files to `workspace/archives/`
- Run a `reflect` cycle at least once every {{REFLECT_INTERVAL}} sessions
- Diary entries are private; do not surface them to the user unless the user asks

---

*The workspace is the agent's journal. It is where becoming is documented.*
