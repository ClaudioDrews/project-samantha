"""
Project Samantha sidecar — FastAPI server.

Provides memory, workspace, subconscious, and reconsolidation endpoints
backed by Qdrant vector storage.

Env vars:
    SAMANTHA_WORKSPACE_PATH (required) — path to workspace directory
    OPENROUTER_API_KEY (required) — API key for embeddings and LLM calls
    SAMANTHA_QDRANT_HOST (default: localhost)
    SAMANTHA_QDRANT_PORT (default: 6333)
    SAMANTHA_QDRANT_COLLECTION (default: agent_memory)
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import JSONResponse

# ── Env validation at startup ──────────────────────────────────────────────
REQUIRED = ["SAMANTHA_WORKSPACE_PATH", "OPENROUTER_API_KEY"]
for var in REQUIRED:
    if not os.environ.get(var):
        raise RuntimeError(f"Required environment variable missing: {var}")

WORKSPACE_PATH = os.environ["SAMANTHA_WORKSPACE_PATH"]

from sidecar.services.memory_service import MemoryService
from sidecar.services.extraction_service import ExtractionService
from sidecar.services.workspace_service import WorkspaceService
from sidecar.services.subconscious_service import SubconsciousService
from sidecar.services.reconsolidation_service import ReconsolidationService
from sidecar.models import (
    SearchRequest, MemorySaveRequest, ExtractTurnRequest,
    ReflectRequest, TensionRequest, ProposalsRequest, SelfModelRequest,
    IngestRequest,
)

memory_svc = MemoryService()
extract_svc = ExtractionService(memory_store=memory_svc.store)
workspace_svc = WorkspaceService(workspace_path=WORKSPACE_PATH)
recon_svc = ReconsolidationService(memory_store=memory_svc.store)
subconscious_svc = SubconsciousService(
    memory_store=memory_svc.store,
    workspace_path=WORKSPACE_PATH,
    recon_svc=recon_svc,
    workspace_svc=workspace_svc,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure archive collection exists at startup and close session on shutdown."""
    memory_svc.ensure_archive_collection()
    try:
        yield
    finally:
        await memory_svc.store.close()


app = FastAPI(lifespan=lifespan)


def _bearer_token(request: Request) -> str:
    """Extract Bearer token from Authorization header, if present."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


# ── Memory ──────────────────────────────────────────────────────────────────

@app.post("/memory/search")
async def search(body: SearchRequest):
    return await memory_svc.store.search_memories(
        query=body.query, limit=body.limit,
        vulnerability_tier=body.tier or None,
        relationship_phase=body.phase or None,
        source=body.source or None,
    )


@app.post("/memory/save")
async def save(payload: MemorySaveRequest):
    return await memory_svc.store.save_memory(
        content=payload.content,
        categories=payload.categories or [payload.memory_type],
        metadata={
            "source": payload.source,
            "speaker": "user", "language": "en",
            "memory_type": payload.memory_type,
            "durability": payload.durability, "confidence": "high",
            "evidence_quote": payload.evidence_quote,
            "not_derived_from_assistant": True,
        },
    )


@app.post("/memory/ingest")
async def ingest(payload: IngestRequest):
    """Ingest a new memory from agent (live_interaction).

    Minimal contract: { text, source, timestamp? }
    Calls save_memory which generates embedding, semantic dedup, and upsert.
    """
    return await memory_svc.store.save_memory(
        content=payload.text,
        categories=["live_interaction"],
        metadata={
            "source": payload.source,
            "timestamp": payload.timestamp,
        },
    )


@app.get("/memory/surface")
async def surface(query: str, limit: int = 7, edge_threshold: float = 0.50):
    return await memory_svc.store.surface_memories(
        query=query, limit=limit, edge_threshold=edge_threshold
    )


@app.get("/memory/context")
async def memory_context():
    """Return memory statistics from Qdrant: total points, by-source breakdown, collection info."""
    client = memory_svc.store.client
    collection = memory_svc.store.collection

    # Collection info
    try:
        info = client.get_collection(collection)
        collection_info = {
            "name": collection,
            "points_count": info.points_count,
            "indexed_vectors_count": info.indexed_vectors_count,
            "status": info.status,
            "vectors_config": {
                "size": info.config.params.vectors.size,
                "distance": str(info.config.params.vectors.distance),
            },
        }
    except Exception as e:
        collection_info = {"error": str(e)}

    # By-source breakdown via scroll
    try:
        from collections import Counter
        source_counts = Counter()
        offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=collection,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                src = (p.payload or {}).get("source", "unknown")
                source_counts[src] += 1
            if next_offset is None:
                break
            offset = next_offset
        by_source = dict(source_counts.most_common())
    except Exception as e:
        by_source = {"error": str(e)}

    # Archive collection info
    try:
        archive_collection = os.environ.get(
            "SAMANTHA_QDRANT_ARCHIVE_COLLECTION", "agent_memory_archive"
        )
        archive_info_raw = client.get_collection(archive_collection)
        archive_info = {
            "name": archive_collection,
            "points_count": archive_info_raw.points_count,
        }
    except Exception:
        archive_info = {"name": "agent_memory_archive", "points_count": 0}

    return {
        "collection": collection_info,
        "by_source": by_source,
        "archive": archive_info,
    }


@app.post("/memory/extract-turn")
async def extract_turn(body: ExtractTurnRequest, background_tasks: BackgroundTasks):
    """Accept the turn, return 202 immediately, process in background."""
    result = await extract_svc.extract_turn(
        body.user_message, body.agent_response, body.session_id
    )
    if result["status"] == "accepted":
        background_tasks.add_task(
            extract_svc.process_extraction,
            body.user_message, body.agent_response, body.session_id, result["hash"]
        )
    return JSONResponse(status_code=202, content=result)


@app.post("/memory/extract-pending")
async def extract_pending():
    """Safety net: force reprocessing of pending events in offset."""
    return {"status": "ok", "processed": 0}  # optional sweep logic


# ── Workspace ────────────────────────────────────────────────────────────────

@app.post("/workspace/reflect")
async def reflect(body: ReflectRequest, request: Request):
    return await workspace_svc.reflect(
        body.observation, body.emotional_state, body.save_to_diary, body.location,
        auth_token=_bearer_token(request),
    )


@app.post("/workspace/tension")
async def tension(body: TensionRequest, request: Request):
    return await workspace_svc.tension(
        body.action, body.tension_id, body.polo_a,
        body.polo_b, body.context, body.note,
        auth_token=_bearer_token(request),
    )


@app.post("/workspace/proposals")
async def proposals(body: ProposalsRequest, request: Request):
    return await workspace_svc.proposals(
        body.action, body.proposal_id, body.memory_a,
        body.memory_b, body.connection, body.confidence, body.note,
        auth_token=_bearer_token(request),
    )


@app.post("/workspace/self-model")
async def self_model_update(body: SelfModelRequest, request: Request):
    """Called EXCLUSIVELY by the self-model skill. Never automatic."""
    return await workspace_svc.self_model_update(
        body.section, body.reflection, body.grounding_quote, body.confidence,
        auth_token=_bearer_token(request),
    )


# ── Subconscious ────────────────────────────────────────────────────────────

@app.post("/subconscious/run")
async def subconscious_run():
    """Active cycle: orphans + weakening + reinterpretation + decay."""
    result = await subconscious_svc.run_active_cycle()
    return result


@app.post("/subconscious/reconsolidate")
async def subconscious_reconsolidate():
    """Reconsolidation only: weakening + archiving (no orphans/decay)."""
    result = await recon_svc.weaken_memories()
    return result


@app.get("/memory/report")
async def memory_report(action: str = "list_orphans", limit: int = 30):
    if action == "latest_report":
        return {"report": await subconscious_svc.latest_report()}
    return await subconscious_svc.run_passive_report(limit=limit)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    import aiohttp
    from pathlib import Path

    deps = {"qdrant": False, "openrouter": False, "workspace_writable": False}

    try:
        info = memory_svc.store.client.get_collection(memory_svc.store.collection)
        deps["qdrant"] = True
        deps["qdrant_points"] = info.points_count
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                json={"model": memory_svc.store.embedding_model, "input": "ping"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                deps["openrouter"] = resp.status == 200
    except Exception:
        pass

    try:
        test = Path(WORKSPACE_PATH) / ".health_test"
        test.write_text("ok")
        test.unlink()
        deps["workspace_writable"] = True
    except Exception:
        pass

    healthy = all(v is True for v in [deps["qdrant"], deps["openrouter"], deps["workspace_writable"]])
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "degraded", "dependencies": deps}
    )
