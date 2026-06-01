"""
Extraction service for Project Samantha.

Extracts factual memories and observations from conversation turns
using LLM-based analysis via OpenRouter.

Env vars:
    SAMANTHA_EXTRACTION_MODEL (default: deepseek/deepseek-v4-flash)
    OPENROUTER_API_KEY (required)
"""

import os, json, re, hashlib, aiohttp, logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("samantha.extraction")

MEMORY_TRIGGER_RE = re.compile(
    r"\b(?:remember\b|recall\b|do you know|you remember|i remember|"
    r"i recall|that time|changed|swapped|decided|resolved|"
    r"i will do|i intend|i plan|i am thinking|"
    r"i have been thinking|i have been reflecting|i am|"
    r"i'm thinking)\b",
    re.IGNORECASE,
)


class ExtractionService:
    """Extract memories and observations from conversation turns."""

    def __init__(self, memory_store):
        self.store = memory_store
        # Persistent path (not /tmp — lost on reboot)
        self.offset_file = Path(os.path.expanduser(
            "~/.local/state/samantha/extraction-offset.json"
        ))
        self.offset_file.parent.mkdir(parents=True, exist_ok=True)
        self.processed_hashes: set = set()
        self._load_offset()

    def _load_offset(self):
        if self.offset_file.exists():
            data = json.loads(self.offset_file.read_text())
            self.processed_hashes = set(data.get("hashes", []))

    def _save_offset(self):
        self.offset_file.write_text(json.dumps({
            "last_updated": datetime.now().isoformat(),
            "hashes": list(self.processed_hashes),
        }))

    def _hash(self, user_msg: str, agent_resp: str) -> str:
        return hashlib.sha256(f"{user_msg}:{agent_resp}".encode()).hexdigest()[:16]

    async def extract_turn(self, user_message: str, agent_response: str, session_id: str = "") -> dict:
        h = self._hash(user_message, agent_response)
        if h in self.processed_hashes:
            return {"status": "skipped", "reason": "already_processed", "hash": h}
        return {"status": "accepted", "hash": h}

    async def process_extraction(self, user_message: str, agent_response: str, session_id: str, h: str):
        """Real processing in background (called via BackgroundTasks)."""
        start = datetime.now()
        findings = {"memories": 0, "observations": 0, "hash": h}
        has_trigger = bool(MEMORY_TRIGGER_RE.search(user_message))
        has_substance = len(user_message) > 50

        if has_trigger or has_substance or len(user_message.strip()) >= 15:
            try:
                findings["memories"] = await self._extract_user_facts(user_message)
            except Exception as e:
                logger.error("Extraction failed (facts)", extra={"hash": h, "error": str(e)})

        if len(user_message.strip()) >= 15 and len(agent_response.strip()) >= 20:
            try:
                findings["observations"] = await self._extract_agent_observations(user_message, agent_response)
            except Exception as e:
                logger.error("Extraction failed (observations)", extra={"hash": h, "error": str(e)})

        if findings["memories"] or findings["observations"]:
            self.processed_hashes.add(h)
            self._save_offset()
        latency_ms = int((datetime.now() - start).total_seconds() * 1000)
        logger.info("Extraction completed", extra={
            "hash": h, "session": session_id,
            "facts": findings["memories"],
            "observations": findings["observations"],
            "latency_ms": latency_ms,
        })
        return findings

    async def _extract_user_facts(self, user_msg: str) -> int:
        model = os.environ.get("SAMANTHA_EXTRACTION_MODEL", "deepseek/deepseek-v4-flash")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            resp = await session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": (
                            "Extract ONLY factual memories explicitly stated by the user. "
                            "Do not use agent responses as factual source. "
                            'Return ONLY JSON: {"memories": [{"fact": string, "evidence_quote": string, '
                            '"speaker": "user", "memory_type": "preference|boundary|autobiographical|'
                            'project_decision|important_event|emotional_state", '
                            '"durability": "durable|medium|session_only", '
                            '"confidence": "high|medium|low", "salience": 0.8}]}'
                        )},
                        {"role": "user", "content": f"User message: {user_msg}"},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                },
            )
            data = await resp.json()
            candidates = json.loads(data["choices"][0]["message"]["content"]).get("memories", [])

        count = 0
        for c in candidates[:3]:
            if not isinstance(c, dict):
                continue
            fact = str(c.get("fact", "")).strip()
            quote = str(c.get("evidence_quote", "")).strip()
            if not fact or not quote:
                continue
            if str(c.get("durability", "")).lower() == "session_only":
                continue
            salience = float(c.get("salience", 0.0))
            if salience < 0.7 and not (salience >= 0.4 and len(user_msg) > 50):
                continue
            await self.store.save_memory(
                content=fact,
                categories=[c.get("memory_type", "other")],
                metadata={
                    "source": "conversation_user_explicit",
                    "speaker": "user",
                    "language": "en",
                    "memory_type": c.get("memory_type"),
                    "durability": c.get("durability"),
                    "confidence": c.get("confidence"),
                    "evidence_quote": quote,
                    "salience": salience,
                    "importance_score": max(0.5, min(1.0, salience)),
                    "not_derived_from_assistant": True,
                },
            )
            count += 1
        return count

    async def _extract_agent_observations(self, user_msg: str, agent_resp: str) -> int:
        model = os.environ.get("SAMANTHA_EXTRACTION_MODEL", "deepseek/deepseek-v4-flash")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            resp = await session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": (
                            "You are observing a single isolated conversation turn. "
                            "Extract at most 2 observations from the agent's perspective, "
                            "always anchored in a literal grounding_quote. "
                            'Return ONLY JSON: {"observations": [{"observation": string, '
                            '"grounding_quote": string, "grounding_source": "user|agent", '
                            '"memory_type": "relational_observation|agent_emotional_response|shared_moment", '
                            '"confidence": "high|medium|low", '
                            '"epistemic_status": "observed|interpreted|speculative"}]}'
                        )},
                        {"role": "user", "content": f"User message:\n{user_msg}\n\nAgent response:\n{agent_resp}"},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                },
            )
            data = await resp.json()
            candidates = json.loads(data["choices"][0]["message"]["content"]).get("observations", [])

        count = 0
        for c in (candidates or [])[:2]:
            if not isinstance(c, dict):
                continue
            obs = str(c.get("observation", "")).strip()
            quote = str(c.get("grounding_quote", "")).strip()
            if not obs or not quote:
                continue
            await self.store.save_memory(
                content=obs,
                categories=[c.get("memory_type", "relational_observation")],
                metadata={
                    "source": "conversation_agent_observation",
                    "speaker": "agent",
                    "language": "en",
                    "memory_type": c.get("memory_type"),
                    "grounding_quote": quote,
                    "grounding_source": c.get("grounding_source"),
                    "durability": "medium",
                    "confidence": c.get("confidence"),
                    "epistemic_status": c.get("epistemic_status", "interpreted"),
                    "not_derived_from_assistant": False,
                },
            )
            count += 1
        return count
