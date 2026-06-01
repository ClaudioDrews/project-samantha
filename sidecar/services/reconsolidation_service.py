"""
Memory reconsolidation: weakening, archiving, and LLM reinterpretation.

This service implements the memory metabolization layer:
- Weakening: strength decays 5% per cycle for memories older than 30 days
- Protection window: strength_protected_until (14 days) prevents immediate decay
- Double protection: human_context=revisado_ok OR importance_score>0.7 OR salience>0.7
- Floor: 0.1 for protected memories
- Removal: strength < 0.1 and unprotected -> archived to archive collection
- Reinterpretation: stagnant memories (surfaced 3+ times but idle >30d)
  get an LLM reinterpretation

Safety controls:
- durability=durable is immune to weakening
- processing_state=resolved is skipped
- Max 300 memories/cycle for weakening
- Max 5 reinterpretations/day
- Fail-open on all paths

Requires: OPENROUTER_API_KEY in environment (for reinterpretation)

Env vars:
    SAMANTHA_DREAMS_PATH (default: ~/.local/state/samantha/DREAMS.md)
    SAMANTHA_WORKSPACE_PATH (optional, used for DREAMS.md fallback)
    SAMANTHA_QDRANT_ARCHIVE_COLLECTION (default: agent_memory_archive)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from pathlib import Path

import aiohttp

logger = logging.getLogger("samantha.reconsolidation")

_WEAKENING_RATE = 0.95          # Decay multiplier per cycle
_STALENESS_DAYS = 30            # Days without surfacing to start decay
_ARCHIVE_THRESHOLD = 0.10       # strength below this -> archive
_MAX_WEAKEN_PER_CYCLE = 300     # Max points processed per cycle
_MAX_REINTERP_PER_CYCLE = 5     # Max reinterpretations per cycle
_STAGNANT_SURFACE_MIN = 3       # Min surface_count to reinterpret
_LLM_MODEL = "deepseek/deepseek-v4-flash"

DREAMS_PATH = Path(
    os.environ.get(
        "SAMANTHA_DREAMS_PATH",
        str(Path.home() / ".local/state/samantha/DREAMS.md"),
    )
)


def _days_ago(iso_string: Optional[str]) -> Optional[int]:
    """Return days since an ISO timestamp, or None if unavailable."""
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string (YYYY-MM-DD or ISO) to datetime."""
    if not date_str:
        return None
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        else:
            return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _append_dreams_log(entry: str) -> None:
    """Append a log entry to DREAMS.md."""
    try:
        DREAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        line = f"\n*{timestamp}* — {entry}"
        with open(DREAMS_PATH, "a") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"Failed to write DREAMS.md: {e}")


class ReconsolidationService:
    """Memory decay, archiving, and LLM reinterpretation."""

    def __init__(self, memory_store: Any):
        """Initialize with a QdrantMemoryStore instance.

        Args:
            memory_store: QdrantMemoryStore from memory_service
        """
        self.store = memory_store
        self.openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.archive_collection = os.environ.get(
            "SAMANTHA_QDRANT_ARCHIVE_COLLECTION", "agent_memory_archive"
        )

    # ── Weakening (strength-based) ────────────────────────────────────────

    async def weaken_memories(
        self,
        limit: int = _MAX_WEAKEN_PER_CYCLE,
        staleness_days: int = _STALENESS_DAYS,
    ) -> dict:
        """Reduce strength for memories older than staleness_days.

        Uses the `strength` field (0.0-1.0) instead of the old importance_score.
        Honors protection window and double protection rules.

        Scans agent_memory in batches via scroll. For each point:
        - Skip if durability=durable, processing_state=resolved
        - Skip if inside strength_protected_until window
        - Force skip if protected: human_context=revisado_ok OR
          importance_score>0.7 OR salience>0.7
        - If age (based on date field) > staleness_days and NOT protected:
          strength *= 0.95
        - If strength < _ARCHIVE_THRESHOLD (0.1):
          -> Protected: floor at 0.1
          -> Unprotected: archive to archive collection
        - Update payload via set_payload

        After weakening pass, archives memories that fell below threshold.

        Returns:
            {"weakened": N, "archived": M, "errors": [...]}
        """
        weakened = 0
        to_archive: list[str] = []
        protected_floors = 0
        skipped_protected = 0
        skipped_window = 0
        errors: list[str] = []
        processed = 0
        offset: str | None = None
        now = datetime.now(timezone.utc)

        try:
            while processed < limit:
                points, next_offset = self.store.client.scroll(
                    collection_name=self.store.collection,
                    limit=min(100, limit - processed),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )

                if not points:
                    break

                for point in points:
                    if processed >= limit:
                        break

                    payload = dict(point.payload or {})
                    pid = str(point.id)

                    # Skip durable and resolved
                    if payload.get("durability") == "durable":
                        continue
                    if payload.get("processing_state") == "resolved":
                        continue

                    # ── Protection window ──────────────────────────────
                    protected_until = payload.get("strength_protected_until")
                    if protected_until:
                        try:
                            pu = datetime.fromisoformat(
                                protected_until.replace("Z", "+00:00")
                            )
                            if pu.tzinfo is None:
                                pu = pu.replace(tzinfo=timezone.utc)
                            if now < pu:
                                skipped_window += 1
                                processed += 1
                                continue
                        except Exception:
                            pass  # Unparseable -> treat as expired

                    # ── Double protection ──────────────────────────────
                    protected = (
                        payload.get("human_context") == "revisado_ok"
                        or float(payload.get("importance_score") or 0) > 0.7
                        or float(payload.get("salience") or 0) > 0.7
                    )
                    if protected:
                        skipped_protected += 1
                        # Still apply decay if very old, but floor at 0.1
                        strength = float(payload.get("strength") or 0.7)

                        date_str = payload.get("date")
                        date_dt = _parse_date(date_str)
                        age = (now - date_dt).days if date_dt else 999

                        if age > staleness_days:
                            new_strength = round(max(_ARCHIVE_THRESHOLD, strength * _WEAKENING_RATE), 4)
                        else:
                            new_strength = strength

                        if new_strength < _ARCHIVE_THRESHOLD:
                            new_strength = _ARCHIVE_THRESHOLD  # floor
                            protected_floors += 1

                        if new_strength != strength:
                            try:
                                self.store.client.set_payload(
                                    collection_name=self.store.collection,
                                    payload={
                                        "strength": new_strength,
                                        "_weakened_at": now.isoformat(),
                                    },
                                    points=[point.id],
                                )
                                weakened += 1
                            except Exception as e:
                                errors.append(f"weaken (protected) failed for {pid}: {e}")

                        processed += 1
                        continue

                    # ── Non-protected: full decay ──────────────────────
                    strength = float(payload.get("strength") or 0.7)

                    # Age from date field (creation date, not last_surfaced)
                    date_str = payload.get("date")
                    date_dt = _parse_date(date_str)
                    age = (now - date_dt).days if date_dt else 999

                    if age > staleness_days:
                        new_strength = round(max(0.01, strength * _WEAKENING_RATE), 4)
                    else:
                        new_strength = strength  # recent -> no decay

                    # ── Archive or update ──────────────────────────────
                    if new_strength < _ARCHIVE_THRESHOLD:
                        to_archive.append(pid)
                        processed += 1
                        continue  # Don't update strength; archive handles it

                    if new_strength != strength:
                        try:
                            self.store.client.set_payload(
                                collection_name=self.store.collection,
                                payload={
                                    "strength": new_strength,
                                    "_weakened_at": now.isoformat(),
                                },
                                points=[point.id],
                            )
                            weakened += 1
                        except Exception as e:
                            errors.append(f"weaken failed for {pid}: {e}")

                    processed += 1

                if next_offset is None:
                    break
                offset = next_offset

        except Exception as e:
            msg = f"weaken_memories scroll failed: {e}"
            logger.warning(msg)
            errors.append(msg)

        # Archive weak memories (archive + delete from active)
        archived = 0
        if to_archive:
            archived = await self._archive_memories(to_archive)
            if archived < len(to_archive):
                errors.append(f"archive partial: {archived}/{len(to_archive)}")

        # Log to DREAMS.md
        summary = (
            f"Reconsolidation: {weakened} weakened, {archived} archived, "
            f"{protected_floors} floored, {skipped_protected} protected, "
            f"{skipped_window} protection window"
        )
        _append_dreams_log(summary)

        return {
            "weakened": weakened,
            "archived": archived,
            "protected_floors": protected_floors,
            "skipped_protected": skipped_protected,
            "skipped_window": skipped_window,
            "errors": errors,
        }

    # ── Archiving ──────────────────────────────────────────────────────────

    async def _archive_memories(self, point_ids: list[str]) -> int:
        """Move memories from active collection to archive collection.

        Retrieves full point (payload + vector), upserts to archive collection
        with archived_at timestamp, then deletes from active collection.

        Fail-open: a single failure does not block the rest.

        Returns:
            Number of memories successfully archived.
        """
        if not point_ids:
            return 0

        archived = 0
        now = datetime.now(timezone.utc).isoformat()

        for pid in point_ids:
            try:
                # Retrieve full point
                points = self.store.client.retrieve(
                    collection_name=self.store.collection,
                    ids=[pid],
                    with_payload=True,
                    with_vectors=True,
                )
                if not points:
                    logger.warning(f"archive: point {pid} not found, skipping")
                    continue

                point = points[0]
                payload = dict(point.payload or {})
                payload["archived_at"] = now
                payload["archived_from"] = self.store.collection
                payload["archived_reason"] = "strength_below_threshold"

                # Upsert to archive
                from qdrant_client.models import PointStruct

                self.store.client.upsert(
                    collection_name=self.archive_collection,
                    points=[
                        PointStruct(
                            id=pid,
                            vector=point.vector,
                            payload=payload,
                        )
                    ],
                )

                # Delete from active collection
                self.store.client.delete(
                    collection_name=self.store.collection,
                    points_selector=[pid],
                )

                archived += 1
                logger.debug(f"archived memory {pid} (strength < {_ARCHIVE_THRESHOLD})")

            except Exception as e:
                logger.warning(f"archive failed for {pid} (fail-open): {e}")

        return archived

    # ── LLM Reinterpretation ───────────────────────────────────────────────

    async def reinterpret_stagnant(
        self,
        limit: int = _MAX_REINTERP_PER_CYCLE,
        staleness_days: int = _STALENESS_DAYS,
    ) -> dict:
        """Use LLM to reinterpret stagnant memories.

        Finds memories with:
        - surface_count >= 3
        - last_surfaced_at > staleness_days ago
        - processing_state != resolved

        Sends each to deepseek-v4-flash for reinterpretation.
        If should_resolve=True, marks as resolved.
        Appends reinterpretation as sit_note.

        Cost: ~$0.002/day (5 calls to flash model)

        Returns:
            {"reinterpreted": N, "resolved": M, "errors": [...]}
        """
        if not self.openrouter_key:
            return {
                "reinterpreted": 0,
                "resolved": 0,
                "errors": ["OPENROUTER_API_KEY not set"],
            }

        candidates = await self._find_stagnant(limit * 3, staleness_days)
        if not candidates:
            return {"reinterpreted": 0, "resolved": 0, "errors": []}

        reinterpreted = 0
        resolved = 0
        errors: list[str] = []

        for mem in candidates[:limit]:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as session:
                    result = await self._call_llm_reinterpret(session, mem)
                if result is None:
                    continue

                reinterpreted += 1

                # Save reinterpretation as sit_note
                note = (
                    f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] "
                    f"LLM reinterpretation: {result.get('reinterpretation', '')[:500]}"
                )
                new_themes = result.get("new_themes", [])

                try:
                    current = self.store.client.retrieve(
                        collection_name=self.store.collection,
                        ids=[mem["id"]],
                        with_payload=True,
                        with_vectors=False,
                    )
                    if current:
                        payload = dict(current[0].payload or {})
                        sit_notes = list(payload.get("sit_notes") or [])
                        sit_notes.append(note)

                        update_payload: dict = {"sit_notes": sit_notes}

                        if result.get("should_resolve"):
                            update_payload["processing_state"] = "resolved"
                            update_payload["resolved_at"] = (
                                datetime.now(timezone.utc).isoformat()
                            )
                            resolved += 1

                        if new_themes:
                            existing_themes = list(payload.get("themes") or [])
                            for t in new_themes:
                                if t not in existing_themes:
                                    existing_themes.append(t)
                            update_payload["themes"] = existing_themes

                        self.store.client.set_payload(
                            collection_name=self.store.collection,
                            payload=update_payload,
                            points=[current[0].id],
                        )

                except Exception as e:
                    msg = f"reinterpret save failed for {mem['id']}: {e}"
                    logger.warning(msg)
                    errors.append(msg)

            except Exception as e:
                msg = f"LLM reinterpret failed for {mem['id']}: {e}"
                logger.warning(msg)
                errors.append(msg)

        return {
            "reinterpreted": reinterpreted,
            "resolved": resolved,
            "errors": errors,
        }

    async def _find_stagnant(
        self, limit: int, staleness_days: int
    ) -> list[dict]:
        """Find memories eligible for reinterpretation.

        Criteria: surface_count >= 3, last_surfaced > staleness_days,
        not resolved.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=staleness_days)
        candidates: list[dict] = []
        offset: str | None = None

        try:
            while len(candidates) < limit:
                points, next_offset = self.store.client.scroll(
                    collection_name=self.store.collection,
                    limit=min(100, limit * 5),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )

                if not points:
                    break

                for point in points:
                    payload = dict(point.payload or {})
                    if payload.get("processing_state") == "resolved":
                        continue

                    sc = int(payload.get("surface_count") or 0)
                    if sc < _STAGNANT_SURFACE_MIN:
                        continue

                    last = payload.get("last_surfaced_at")
                    if not last:
                        continue

                    try:
                        dt = datetime.fromisoformat(
                            last.replace("Z", "+00:00")
                        )
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt > cutoff:
                            continue
                    except Exception:
                        continue

                    candidates.append(
                        {
                            "id": str(point.id),
                            "text": payload.get("text", ""),
                            "surface_count": sc,
                            "strength": payload.get("strength", 0.7),
                        }
                    )

                    if len(candidates) >= limit:
                        break

                if next_offset is None:
                    break
                offset = next_offset

        except Exception as e:
            logger.warning(f"_find_stagnant scroll failed (fail-open): {e}")

        return candidates

    async def _call_llm_reinterpret(
        self, session: aiohttp.ClientSession, mem: dict
    ) -> Optional[dict]:
        """Call OpenRouter for memory reinterpretation.

        Args:
            session: Shared aiohttp session
            mem: {"id": str, "text": str, "surface_count": int,
                  "strength": float}

        Returns:
            Parsed JSON response or None on failure.
        """
        prompt = (
            "You are Samantha, a cognitive agent. This memory has been accessed "
            f"{mem['surface_count']} times but has been stagnant for over 30 days.\n\n"
            f"Original memory: {mem['text']}\n\n"
            "Reinterpret this memory in the current context (2026). "
            "Is it still relevant? Should it be marked as resolved "
            "(metabolized) or should it remain active?\n\n"
            "Return ONLY JSON, no markdown, no explanation:\n"
            '{"reinterpretation": "your reinterpretation in English", '
            '"should_resolve": true/false, '
            '"new_themes": ["theme1", "theme2"]}'
        )

        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openrouter_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 400,
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(
                        f"LLM reinterpret HTTP {resp.status}: {text[:200]}"
                    )
                    return None

                data = await resp.json()
                content = data["choices"][0]["message"]["content"]

                # Extract JSON from response (may contain markdown fences)
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()

                return json.loads(content)

        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"LLM reinterpret call failed: {e}")
            return None


