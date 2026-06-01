"""
Pydantic models for Project Samantha sidecar API.

These models define the request/response schemas for all endpoints.
"""

from pydantic import BaseModel
from typing import Optional, List, Literal


class SearchRequest(BaseModel):
    """Search memory by semantic query with optional filters."""
    query: str = Field(..., max_length=4000)
    limit: int = Field(default=7, ge=1, le=50)
    tier: str = Field(default="", max_length=100)
    phase: str = Field(default="", max_length=100)
    source: str = Field(default="", max_length=100)


class MemorySaveRequest(BaseModel):
    """Direct memory save request."""
    source: str = Field(..., max_length=200)
    content: str = Field(..., max_length=10000)
    evidence_quote: str = Field(default="", max_length=5000)
    memory_type: str = Field(default="", max_length=100)
    durability: str = Field(default="", max_length=50)
    categories: Optional[List[str]] = None


class ExtractTurnRequest(BaseModel):
    """Extract memories from a conversation turn."""
    user_message: str = Field(..., max_length=4000)
    agent_response: str = Field(..., max_length=10000)
    session_id: str = Field(default="", max_length=100)
    turn_timestamp: str = Field(default="", max_length=50)


class ReflectRequest(BaseModel):
    """Workspace reflection entry."""
    observation: str = Field(..., max_length=5000)
    emotional_state: str = Field(..., max_length=2000)
    save_to_diary: bool = False
    location: str = Field(default="", max_length=500)


class TensionRequest(BaseModel):
    """Manage cognitive tensions."""
    action: Literal["list", "add", "sit", "resolve"]
    tension_id: str = Field(default="", max_length=50)
    polo_a: str = Field(default="", max_length=2000)
    polo_b: str = Field(default="", max_length=2000)
    context: str = Field(default="", max_length=5000)
    note: str = Field(default="", max_length=2000)


class ProposalsRequest(BaseModel):
    """Manage memory integration proposals."""
    action: Literal["list", "add_draft", "dismiss", "mark_reviewed"]
    proposal_id: str = Field(default="", max_length=50)
    memory_a: str = Field(default="", max_length=2000)
    memory_b: str = Field(default="", max_length=2000)
    connection: str = Field(default="", max_length=5000)
    confidence: str = Field(default="low", max_length=20)
    note: str = Field(default="", max_length=2000)


class SelfModelRequest(BaseModel):
    """Update self-model section.

    section values: relational_patterns | sensitivities | processing_modes | self_image
    confidence values: high | medium | low
    """
    section: Literal["relational_patterns", "sensitivities", "processing_modes", "self_image"]
    reflection: str = Field(..., max_length=5000)
    grounding_quote: str = Field(..., max_length=2000)
    confidence: Literal["high", "medium", "low"]


class IngestRequest(BaseModel):
    """Ingest a new memory from agent interaction."""
    text: str = Field(..., max_length=10000)
    source: str = Field(default="live_interaction", max_length=200)
    timestamp: Optional[str] = Field(default=None, max_length=50)
