"""
Subconscious service for Project Samantha.

Memory introspection: orphans, reconsolidation, and reporting.

Passive mode (default): identifies orphaned/underused memories,
writes SUBCONSCIOUS_LOG.md. Does not mutate Qdrant.

Active mode: runs the full metabolization cycle — orphan detection,
weakening, LLM reinterpretation, and proposal decay.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

logger = logging.getLogger("samantha.subconscious")


class SubconsciousService:
    """Memory introspection: orphans, reconsolidation, and reporting."""

    def __init__(
        self,
        memory_store: Any,
        workspace_path: str,
        recon_svc: Optional[Any] = None,
        workspace_svc: Optional[Any] = None,
    ):
        """Initialize with optional reconsolidation and workspace services.

        Args:
            memory_store: QdrantMemoryStore for memory access
            workspace_path: Path to Samantha workspace directory
            recon_svc: ReconsolidationService (optional; enables active cycle)
            workspace_svc: WorkspaceService (optional; enables proposal decay)
        """
        self.store = memory_store
        self.path = Path(workspace_path)
        self.recon_svc = recon_svc
        self.workspace_svc = workspace_svc

    # ── Passive report ───────────────────────────────────────────────

    async def run_passive_report(self, limit: int = 50) -> dict:
        """Identify orphaned/underused memories without mutation.

        Scans the collection for:
        - Memories with surface_count == 0 (never surfaced)
        - Memories with low strength (< 0.3)
        - Memories idle (last_surfaced_at older than 30 days)

        Returns:
            {"orphans": [...], "underused": N, "idle": N}
        """
        orphans = []
        underused_count = 0
        idle_count = 0
        errors: list[str] = []

        try:
            offset: str | None = None
            processed = 0
            now = datetime.now()

            while processed < limit * 2:
                points, next_offset = self.store.client.scroll(
                    collection_name=self.store.collection,
                    limit=min(100, limit * 2 - processed),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )

                if not points:
                    break

                for point in points:
                    if processed >= limit * 2:
                        break
                    processed += 1

                    payload = dict(point.payload or {})
                    pid = str(point.id)
                    text = payload.get("text", "") or str(payload.get("id", ""))
                    surface_count = int(payload.get("surface_count") or 0)
                    strength = float(payload.get("strength") or 0.0)

                    # Never surfaced
                    if surface_count == 0 and strength > 0:
                        orphans.append({
                            "id": pid,
                            "text": text[:200],
                            "reason": "never_surfaced",
                            "strength": strength,
                        })
                        continue

                    # Low strength
                    if strength < 0.3 and surface_count <= 2:
                        orphans.append({
                            "id": pid,
                            "text": text[:200],
                            "reason": "low_strength",
                            "strength": strength,
                        })
                        continue

                    # Idle (not surfaced in 30 days)
                    last_surfaced = payload.get("last_surfaced_at")
                    if last_surfaced:
                        try:
                            dt = datetime.fromisoformat(
                                last_surfaced.replace("Z", "+00:00")
                            )
                            age_days = (now - dt).days
                            if age_days > 30:
                                idle_count += 1
                                if len(orphans) < limit:
                                    orphans.append({
                                        "id": pid,
                                        "text": text[:200],
                                        "reason": "idle_30d",
                                        "last_surfaced_days_ago": age_days,
                                    })
                        except Exception:
                            pass

                    if len(orphans) >= limit:
                        break

                if next_offset is None:
                    break
                offset = next_offset

        except Exception as e:
            msg = f"Passive report scroll failed: {e}"
            logger.warning(msg)
            errors.append(msg)

        underused_count = sum(
            1 for o in orphans if o.get("reason") in ("never_surfaced", "low_strength")
        )

        return {
            "orphans": orphans[:limit],
            "underused": underused_count,
            "idle": idle_count,
            "total_scanned": processed,
            "errors": errors,
        }

    # ── Active cycle ───────────────────────────────────────────────────────

    async def run_active_cycle(
        self,
        weaken_limit: int = 300,
        reinterpret_limit: int = 5,
        orphan_limit: int = 50,
    ) -> dict:
        """Full metabolization cycle: orphans, weakening, reinterpretation.

        Combines passive orphan detection with active memory
        reconsolidation (weakening + archiving + LLM reinterpretation)
        and proposal decay.

        Fails gracefully: each stage proceeds even if earlier stages
        had errors. The SUBCONSCIOUS_LOG.md is written with all findings.

        Returns:
            Summary dict with counts from all stages.
        """
        findings: dict = {
            "orphans": [],
            "weakened": 0,
            "archived": 0,
            "reinterpreted": 0,
            "resolved": 0,
            "proposals_dismissed": 0,
            "errors": [],
        }

        # Stage 1: Orphan detection (passive, always runs)
        try:
            passive = await self.run_passive_report(limit=orphan_limit)
            findings["orphans"] = passive.get("orphans", [])
        except Exception as e:
            findings["errors"].append(f"orphan detection: {e}")

        # Stage 2: Weakening + archiving (requires recon_svc)
        if self.recon_svc is not None:
            try:
                result = await self.recon_svc.weaken_memories(
                    limit=weaken_limit
                )
                findings["weakened"] = result.get("weakened", 0)
                findings["archived"] = result.get("archived", 0)
                if result.get("errors"):
                    findings["errors"].extend(result["errors"])
            except Exception as e:
                findings["errors"].append(f"weakening: {e}")

        # Stage 3: LLM reinterpretation (requires recon_svc)
        if self.recon_svc is not None:
            try:
                result = await self.recon_svc.reinterpret_stagnant(
                    limit=reinterpret_limit
                )
                findings["reinterpreted"] = result.get("reinterpreted", 0)
                findings["resolved"] = result.get("resolved", 0)
                if result.get("errors"):
                    findings["errors"].extend(result["errors"])
            except Exception as e:
                findings["errors"].append(f"reinterpretation: {e}")

        # Stage 4: Proposal decay (requires workspace_svc)
        if self.workspace_svc is not None:
            try:
                result = await self.workspace_svc.decay_proposals()
                findings["proposals_dismissed"] = result.get("dismissed", 0)
            except Exception as e:
                findings["errors"].append(f"proposal decay: {e}")

        # Write consolidated report
        self._write_active_log(findings)

        return {
            "status": "ok",
            "orphans": len(findings["orphans"]),
            "weakened": findings["weakened"],
            "archived": findings["archived"],
            "reinterpreted": findings["reinterpreted"],
            "resolved": findings["resolved"],
            "proposals_dismissed": findings["proposals_dismissed"],
            "errors": len(findings["errors"]),
        }

    def _write_active_log(self, findings: dict) -> None:
        """Append active cycle findings to SUBCONSCIOUS_LOG.md."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        orphans = findings.get("orphans", [])

        lines = [
            f"\n## Active Report — {now}",
            "",
            f"**Orphans:** {len(orphans)}",
            f"**Weakened:** {findings.get('weakened', 0)}",
            f"**Archived:** {findings.get('archived', 0)}",
            f"**Reinterpreted:** {findings.get('reinterpreted', 0)}",
            f"**Resolved:** {findings.get('resolved', 0)}",
            f"**Proposals dismissed:** {findings.get('proposals_dismissed', 0)}",
            "",
        ]

        if findings.get("errors"):
            lines.append("### Errors")
            for err in findings["errors"]:
                lines.append(f"- {err}")
            lines.append("")

        if orphans:
            lines.append("### Detected orphans")
            for item in orphans[:10]:
                lines.append(
                    f"- `{item.get('id', '?')}` — "
                    f"{item.get('text', '?')[:120]}"
                )

        self.path.mkdir(parents=True, exist_ok=True)
        log_path = self.path / "SUBCONSCIOUS_LOG.md"

        if log_path.exists():
            existing = log_path.read_text()
        else:
            existing = (
                "# SUBCONSCIOUS_LOG.md\n\n"
                "Memory metabolization report.\n"
            )

        log_path.write_text(existing.rstrip() + "\n" + "\n".join(lines))

    # ── Latest report ──────────────────────────────────────────────────────

    async def latest_report(self) -> str:
        """Return tail of SUBCONSCIOUS_LOG.md."""
        log_path = self.path / "SUBCONSCIOUS_LOG.md"
        if not log_path.exists():
            return "No SUBCONSCIOUS_LOG.md found."
        return log_path.read_text()[-4000:]
