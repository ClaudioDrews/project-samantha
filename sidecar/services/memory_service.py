"""
Memory service for Project Samantha.

Provides a Qdrant-backed memory store for semantic search and archival.
Uses local qdrant_store module — no external package dependencies.

Env vars:
    SAMANTHA_QDRANT_HOST (default: localhost)
    SAMANTHA_QDRANT_PORT (default: 6333)
    SAMANTHA_QDRANT_COLLECTION (default: agent_memory)
    SAMANTHA_EMBEDDING_MODEL (default: qwen/qwen3-embedding-8b)
    SAMANTHA_EMBEDDING_DIM (default: 4096)
    SAMANTHA_EMBEDDING_INSTRUCTION (default: "")
    OPENROUTER_API_KEY (required for embeddings)
"""

import os

from sidecar.services.qdrant_store import QdrantMemoryStore


class MemoryService:
    """Manages Qdrant memory store lifecycle."""

    def __init__(self):
        self.store = QdrantMemoryStore(
            host=os.environ.get("SAMANTHA_QDRANT_HOST", "localhost"),
            port=int(os.environ.get("SAMANTHA_QDRANT_PORT", "6333")),
            collection=os.environ.get("SAMANTHA_QDRANT_COLLECTION", "agent_memory"),
            embedding_model=os.environ.get("SAMANTHA_EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"),
            embedding_dim=int(os.environ.get("SAMANTHA_EMBEDDING_DIM", "4096")),
            embedding_instruction=os.environ.get("SAMANTHA_EMBEDDING_INSTRUCTION", ""),
            openrouter_key=os.environ.get("OPENROUTER_API_KEY", ""),
        )

    def ensure_archive_collection(self):
        """Create agent_memory and agent_memory_archive if they don't exist. Called at startup."""
        from qdrant_client.models import VectorParams, Distance

        client = self.store.client

        # Ensure active collection exists
        if not client.collection_exists(self.store.collection):
            client.create_collection(
                collection_name=self.store.collection,
                vectors_config=VectorParams(
                    size=self.store.embedding_dim,
                    distance=Distance.COSINE,
                ),
            )

        # Ensure archive collection exists
        archive_collection = os.environ.get(
            "SAMANTHA_QDRANT_ARCHIVE_COLLECTION", "agent_memory_archive"
        )
        if not client.collection_exists(archive_collection):
            client.create_collection(
                collection_name=archive_collection,
                vectors_config=VectorParams(
                    size=self.store.embedding_dim,
                    distance=Distance.COSINE,
                ),
            )
