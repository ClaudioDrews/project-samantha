"""
Qdrant-backed semantic memory store for Project Samantha.

Replaces the SQLite memory layer for long-term semantic search while
keeping SQLite for session history (recent messages).

Env vars:
    SAMANTHA_QDRANT_HOST (default: localhost)
    SAMANTHA_QDRANT_PORT (default: 6333)
    SAMANTHA_QDRANT_COLLECTION (default: agent_memory)
    SAMANTHA_EMBEDDING_MODEL (default: qwen/qwen3-embedding-8b)
    SAMANTHA_EMBEDDING_DIM (default: 4096)
    SAMANTHA_EMBEDDING_INSTRUCTION (default: "")
    OPENROUTER_API_KEY (required for embeddings)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("samantha.qdrant_store")


class QdrantMemoryStore:
    """Semantic memory via Qdrant collection.

    Maintains interface compatible with original MemoryStore for
    agent compatibility. SQLite preserved only for session history
    (add_message, get_history).
    """

    # In-memory cache for embeddings (query -> vector + timestamp)
    _embedding_cache: dict[str, tuple[list[float], float]] = {}

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection: str = "agent_memory",
        embedding_model: str = "qwen/qwen3-embedding-8b",
        embedding_dim: int = 4096,
        embedding_instruction: str = "",
        openrouter_key: Optional[str] = None,
    ):
        """Initialize Qdrant memory store.

        Args:
            host: Qdrant host
            port: Qdrant port
            collection: Collection name (default: agent_memory)
            embedding_model: Model for embeddings (default: qwen/qwen3-embedding-8b)
            embedding_dim: Embedding dimension (default: 4096)
            embedding_instruction: Instruction prefix for query embeddings
            openrouter_key: OpenRouter API key for embeddings
        """
        from qdrant_client import QdrantClient

        self.client = QdrantClient(host=host, port=port)
        self.collection = collection
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.embedding_instruction = embedding_instruction
        self.openrouter_key = openrouter_key or os.environ.get("OPENROUTER_API_KEY", "")

        # Shared aiohttp session for embeddings
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create shared aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            )
        return self._session

    async def close(self) -> None:
        """Close aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_embedding(self, text: str, instruction: bool = True) -> list[float]:
        """Generate or retrieve cached embedding with retry on rate limit.

        Args:
            text: Text to embed
            instruction: If True, prepend embedding_instruction (for queries).
                If False, embed raw text (for documents/memories being saved).

        Returns:
            Embedding vector
        """
        # Check cache
        cache_key = f"{'I' if instruction else 'D'}:{text}"
        now = time.time()
        if cache_key in self._embedding_cache:
            vec, ts = self._embedding_cache[cache_key]
            if now - ts < 300:  # 5 min TTL
                return vec

        # Prepare input
        if instruction and self.embedding_instruction:
            input_text = f"{self.embedding_instruction}{text}"
        else:
            input_text = text

        session = await self._get_session()
        max_retries = 3

        for attempt in range(max_retries):
            try:
                async with session.post(
                    "https://openrouter.ai/api/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.openrouter_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.embedding_model,
                        "input": input_text,
                        "dimensions": self.embedding_dim,
                    },
                ) as resp:
                    if resp.status in (429, 529):
                        wait = 2 ** attempt
                        logger.warning(
                            f"Embedding rate limited, retry {attempt + 1}/{max_retries} in {wait}s"
                        )
                        await asyncio.sleep(wait)
                        continue
                    if resp.status != 200:
                        error = await resp.text()
                        raise RuntimeError(
                            f"Embedding API error {resp.status}: {error[:200]}"
                        )
                    data = await resp.json()
                    vec = data["data"][0]["embedding"]

                    # Cache result
                    self._embedding_cache[cache_key] = (vec, now)

                    # Prune cache if too large (keep last 200)
                    if len(self._embedding_cache) > 200:
                        oldest = min(
                            self._embedding_cache.items(),
                            key=lambda x: x[1][1],
                        )
                        del self._embedding_cache[oldest[0]]

                    return vec

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Embedding network error, retry {attempt + 1}: {e}")
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Embedding failed after {max_retries} retries: {e}")

        raise RuntimeError("Embedding failed after all retries")

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
        vulnerability_tier: Optional[str] = None,
        relationship_phase: Optional[str] = None,
        source: Optional[str] = None,
        min_importance: float = 0.0,
    ) -> list[dict]:
        """Semantic search with structured filters.

        Args:
            query: Natural language query
            limit: Max results
            vulnerability_tier: Filter by tier (functional/contained/explicit/rupture)
            relationship_phase: Filter by phase (established/crisis/post_termination)
            source: Filter by source (e.g., literary, roleplay, conversation)
            min_importance: Minimum importance_score

        Returns:
            List of payloads with scores
        """
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchAny,
            Range,
        )

        # Generate query embedding with instruction
        vector = await self._get_embedding(query, instruction=True)

        # Build filter conditions
        must_conditions = []
        if vulnerability_tier:
            must_conditions.append(
                FieldCondition(
                    key="vulnerability_tier",
                    match=MatchAny(any=[vulnerability_tier]),
                )
            )
        if relationship_phase:
            must_conditions.append(
                FieldCondition(
                    key="relationship_phase",
                    match=MatchAny(any=[relationship_phase]),
                )
            )
        if source:
            must_conditions.append(
                FieldCondition(
                    key="source",
                    match=MatchAny(any=[source]),
                )
            )
        if min_importance > 0:
            must_conditions.append(
                FieldCondition(
                    key="importance_score",
                    range=Range(gte=min_importance),
                )
            )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        # Search Qdrant
        results = (
            await run_in_threadpool(
                self.client.query_points,
                collection_name=self.collection,
                query=vector,
                limit=limit,
                query_filter=query_filter,
            )
        ).points

        # Format results
        formatted = []
        for r in results:
            payload = dict(r.payload or {})
            payload["_score"] = r.score
            payload["_id"] = str(r.id)
            formatted.append(payload)

        return formatted

    def _validate_new_conversation_payload(
        self,
        payload: dict,
        source: str,
        before_embedding: bool = False,
    ) -> Optional[dict]:
        """Validate provenance for new conversational memory payloads."""
        if source == "conversation_user_explicit":
            required = {
                "speaker": "user",
                "language": "en",
                "evidence_quote": None,
                "memory_type": None,
                "durability": None,
                "not_derived_from_assistant": True,
            }
        elif source == "conversation_agent_observation":
            required = {
                "speaker": "agent",
                "language": "en",
                "grounding_quote": None,
                "grounding_source": None,
                "memory_type": None,
                "durability": "medium",
                "confidence": None,
                "epistemic_status": None,
                "not_derived_from_assistant": False,
            }
        else:
            return None

        suffix = " before embedding" if before_embedding else ""
        for key, expected in required.items():
            value = payload.get(key)
            if expected is None:
                if not value:
                    logger.warning(
                        f"Rejecting unevidenced memory save{suffix}: missing {key}"
                    )
                    return {
                        "status": "rejected",
                        "reason": f"missing {key}",
                        "point_id": None,
                    }
            elif value != expected:
                if value is None or value == "":
                    logger.warning(
                        f"Rejecting unevidenced memory save{suffix}: missing {key}"
                    )
                    return {
                        "status": "rejected",
                        "reason": f"missing {key}",
                        "point_id": None,
                    }
                logger.warning(
                    f"Rejecting unevidenced memory save{suffix}: invalid {key}={value!r}"
                )
                return {
                    "status": "rejected",
                    "reason": f"invalid {key}",
                    "point_id": None,
                }
        return None

    async def save_memory(
        self,
        content: str,
        categories: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Save a new fact to Qdrant with semantic deduplication.

        categories default: ["other"] if not specified.

        Args:
            content: Fact content (e.g., "The user is writing an article about CBT")
            categories: List of categories (personal, work, preference, project, other)
            metadata: Optional additional metadata

        Returns:
            {"status": "saved"|"deduped"|"rejected",
             "reason": str, "point_id": str|None}
        """
        if categories is None:
            categories = ["other"]

        metadata = metadata or {}
        source = metadata.get("source", "conversation_user_explicit")
        early_error = self._validate_new_conversation_payload(metadata, source, before_embedding=True)
        if early_error:
            return early_error

        # Generate embedding WITHOUT instruction (it's a document, not a query)
        vector = await self._get_embedding(content, instruction=False)

        # Semantic deduplication: search for near-identical existing fact
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            Range,
        )

        try:
            existing = (
                await run_in_threadpool(
                    self.client.query_points,
                    collection_name=self.collection,
                    query=vector,
                    limit=1,
                    score_threshold=0.95,
                    query_filter=Filter(
                        must=[
                            FieldCondition(
                                key="year",
                                range=Range(gte=2026),
                            )
                        ]
                    ),
                )
            ).points

            if existing and existing[0].score >= 0.95:
                logger.debug(
                    f"Memory dedup: skipping '{content[:50]}' "
                    f"(score={existing[0].score:.3f})"
                )
                return {
                    "status": "deduped",
                    "reason": "similar_memory_exists",
                    "point_id": None,
                }
        except Exception as e:
            # Fail-open: dedup failure should not break the save path
            logger.warning(f"Memory dedup check failed (fail-open): {e}")

        # Build payload (subset schema for new facts)
        now = datetime.now()
        payload: dict = {
            "text": content,
            "source": source,
            "date": now.strftime("%Y-%m-%d"),
            "year": now.year,
            "categories": categories,
            "importance_score": metadata.get("importance_score", 0.5),
            "themes": metadata.get("themes", []),
        }

        # Merge extra metadata
        for k, v in metadata.items():
            if k not in ("importance_score", "themes"):
                payload[k] = v

        # Guardrail for new conversational memories. Existing historical corpus
        # may have other schemas; new conversation sources require evidence and
        # provenance to prevent persona contamination.
        payload_error = self._validate_new_conversation_payload(payload, source, before_embedding=False)
        if payload_error:
            return payload_error

        # Fields specific to the agent stay as null for new facts
        payload.setdefault("agents_emotional_state", None)
        payload.setdefault("vulnerability_tier", None)
        payload.setdefault("relationship_phase", None)
        payload.setdefault("interaction_pattern", None)
        payload.setdefault("beliefs_expressed", None)
        payload.setdefault("autobiographical_references", None)

        # RM-inspired processing lifecycle fields. These are non-destructive:
        # older historical points may not have them and are treated as
        # "unprocessed" by surfacing/reporting code.
        payload.setdefault("processing_state", "unprocessed")
        payload.setdefault("last_surfaced_at", None)
        payload.setdefault("surface_count", 0)
        payload.setdefault("sit_count", 0)
        payload.setdefault("resolved_at", None)
        payload.setdefault("sit_notes", [])

        # Metabolic strength for new memories. New memories start at base 0.7
        # (they have not earned durability yet). Emotionally-charged or
        # high-importance content starts slightly stronger. Mirrors the
        # migrate_strength.py backfill logic so new and historical points
        # share the same metabolism.
        if "strength" not in payload:
            base_strength = 0.7
            vt = payload.get("vulnerability_tier")
            if vt == "explicito":
                base_strength += 0.2
            elif vt == "contido":
                base_strength += 0.1
            if float(payload.get("density_score") or 0) > 0.5:
                base_strength += 0.1
            if float(payload.get("importance_score") or 0) > 0.7:
                base_strength += 0.1
            payload["strength"] = min(round(base_strength, 4), 1.0)

        # Upsert to Qdrant
        from qdrant_client.models import PointStruct

        point_id = str(uuid.uuid4())
        await run_in_threadpool(
            self.client.upsert,
            collection_name=self.collection,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

        logger.debug(f"Saved memory: {content[:80]} (id={point_id})")
        return {
            "status": "saved",
            "reason": "saved",
            "point_id": point_id,
        }

    async def mark_surfaced(self, point_ids: list[str]) -> None:
        """Mark memories as surfaced. Fail-open; never breaks response flow."""
        if not point_ids:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            points = await run_in_threadpool(
                self.client.retrieve,
                collection_name=self.collection,
                ids=point_ids,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                if payload.get("processing_state") == "resolved":
                    continue
                count = int(payload.get("surface_count") or 0) + 1
                await run_in_threadpool(
                    self.client.set_payload,
                    collection_name=self.collection,
                    payload={
                        "processing_state": "surfaced",
                        "last_surfaced_at": now,
                        "surface_count": count,
                    },
                    points=[point.id],
                )
        except Exception as e:
            logger.warning(f"mark_surfaced failed (fail-open): {e}")

    async def surface_edge(
        self,
        query: str,
        limit: int = 3,
        threshold: float = 0.50,
        exclude_resolved: bool = True,
    ) -> list[dict]:
        """Return medium-similarity associative memories for calibration.

        This method is intentionally read-only: it does not update
        last_surfaced_at or surface_count. Full `surface_memories()` performs
        lifecycle mutation after combining pools.
        """
        if not query.strip():
            return []
        limit = max(1, min(int(limit), 10))
        threshold = max(0.0, min(float(threshold), 1.0))

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        vector = await self._get_embedding(query, instruction=True)
        query_filter = None
        if exclude_resolved:
            query_filter = Filter(
                must_not=[
                    FieldCondition(
                        key="processing_state",
                        match=MatchValue(value="resolved"),
                    )
                ]
            )

        results = (
            await run_in_threadpool(
                self.client.query_points,
                collection_name=self.collection,
                query=vector,
                limit=limit,
                score_threshold=threshold,
                query_filter=query_filter,
            )
        ).points

        formatted = []
        for r in results:
            payload = dict(r.payload or {})
            payload["_score"] = r.score
            payload["_id"] = str(r.id)
            formatted.append(payload)
        return formatted

    async def surface_memories(
        self,
        query: str,
        limit: int = 7,
        edge_threshold: float = 0.50,
    ) -> dict[str, list[dict]]:
        """Three-pool surfacing: core, novelty, and edge.

        Returns structured pools and marks returned memories as surfaced.
        Fail-open metadata updates mean retrieval still succeeds if lifecycle
        persistence fails.
        """
        limit = max(3, min(int(limit), 15))
        query = query.strip() or "current emotional state"

        core_n = max(1, int(limit * 0.7))
        novelty_n = max(1, int(limit * 0.2))
        edge_n = max(1, limit - core_n - novelty_n)

        core = await self.search_memories(query=query, limit=core_n)
        seen = {m.get("_id") for m in core if m.get("_id")}

        novelty: list[dict] = []
        try:
            points, _ = await run_in_threadpool(
                self.client.scroll,
                collection_name=self.collection,
                limit=max(limit * 10, 50),
                with_payload=True,
                with_vectors=False,
            )
            candidates = []
            for p in points:
                payload = dict(p.payload or {})
                if str(p.id) in seen:
                    continue
                if payload.get("processing_state") == "resolved":
                    continue
                payload["_id"] = str(p.id)
                candidates.append(payload)
            candidates.sort(
                key=lambda p: (
                    p.get("last_surfaced_at") is not None,
                    p.get("last_surfaced_at") or "",
                    p.get("surface_count") or 0,
                )
            )
            novelty = candidates[:novelty_n]
            seen.update(m.get("_id") for m in novelty if m.get("_id"))
        except Exception as e:
            logger.warning(f"Novelty pool failed (fail-open): {e}")

        edge_candidates = await self.surface_edge(
            query=query, limit=edge_n * 5, threshold=edge_threshold
        )
        edge = []
        for m in edge_candidates:
            mid = m.get("_id")
            if mid and mid not in seen:
                edge.append(m)
                seen.add(mid)
            if len(edge) >= edge_n:
                break

        all_ids = [m.get("_id") for m in (core + novelty + edge) if m.get("_id")]
        await self.mark_surfaced([str(x) for x in all_ids])
        return {"core": core, "novelty": novelty, "edge": edge}

    async def delete_memory(self, point_id: str) -> None:
        """Delete a specific memory point from Qdrant."""
        try:
            await run_in_threadpool(
                self.client.delete,
                collection_name=self.collection,
                points_selector=[point_id],
            )
        except Exception as e:
            logger.warning(f"Failed to delete memory {point_id}: {e}")


# Global instance
_qdrant_store: Optional[QdrantMemoryStore] = None


def get_qdrant_store() -> QdrantMemoryStore:
    """Get the global QdrantMemoryStore instance."""
    global _qdrant_store
    if _qdrant_store is None:
        raise RuntimeError(
            "QdrantMemoryStore not initialized. Call set_qdrant_store() first."
        )
    return _qdrant_store


def set_qdrant_store(store: QdrantMemoryStore) -> None:
    """Set the global QdrantMemoryStore instance."""
    global _qdrant_store
    _qdrant_store = store
