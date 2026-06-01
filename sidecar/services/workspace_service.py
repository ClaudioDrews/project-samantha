"""
Workspace service for Project Samantha.

Manages workspace files (STATE.md, SELF_MODEL.md, TENSIONS.md, PROPOSALS.md)
with auto-commit to git.
"""

import asyncio, re, os
from datetime import datetime
from pathlib import Path
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool


def as_workspace_data(user_text: str) -> str:
    """Sanitize user-provided text to prevent prompt injection via markdown.

    - Replaces triple backticks with a visually neutral alternative
      (backtick + zero-width space + double backtick) so code fences
      cannot be forged.
    - Wraps the result in a ```text code block to contain any
      remaining markdown syntax.

    Args:
        user_text: Raw user-supplied text.

    Returns:
        Sanitized text safe for writing into agent instruction files.
    """
    sanitized = user_text.replace("```", "`\u200b``")
    return f"```text\n{sanitized}\n```"


class WorkspaceService:
    """Manage workspace markdown files with git auto-commit."""

    def __init__(self, workspace_path: str):
        self.path = Path(workspace_path)
        self._pending_commits: list[str] = []
        self._commit_handle = None
        self._commit_lock = asyncio.Lock()
        self._file_lock = asyncio.Lock()
        self._auth_token = os.environ.get("SAMANTHA_AUTH_TOKEN", "")
        self._ensure_git()

    def _check_auth(self, token: str = "") -> None:
        """Raise HTTPException if auth token is configured and doesn't match."""
        if self._auth_token and token != self._auth_token:
            raise HTTPException(status_code=401, detail="Invalid or missing authorization token")

    def _ensure_git(self):
        if not (self.path / ".git").exists():
            import subprocess
            subprocess.run(["git", "init"], cwd=self.path, capture_output=True)
            subprocess.run(["git", "config", "user.email", "samantha@local"], cwd=self.path, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Samantha Sidecar"], cwd=self.path, capture_output=True)

    async def _read_file(self, path: Path) -> str:
        """Read a file off the event loop using thread pool."""
        return await run_in_threadpool(path.read_text)

    async def _write_file(self, path: Path, content: str) -> None:
        """Write a file off the event loop using thread pool."""
        await run_in_threadpool(path.write_text, content)

    async def _queue_commit(self, message: str):
        """Accumulate commits; flush in batch after 30s or 5 mutations."""
        async with self._commit_lock:
            self._pending_commits.append(message)
            if len(self._pending_commits) >= 5:
                await self._flush_commits_inner()
            elif self._commit_handle is None:
                loop = asyncio.get_event_loop()
                self._commit_handle = loop.call_later(
                    30.0, lambda: asyncio.create_task(self._flush_commits())
                )

    async def _flush_commits(self):
        async with self._commit_lock:
            await self._flush_commits_inner()

    async def _flush_commits_inner(self):
        """Called with lock already acquired."""
        if self._commit_handle:
            self._commit_handle.cancel()
            self._commit_handle = None
        if not self._pending_commits:
            return
        composed = " | ".join(self._pending_commits)
        for args in [["git", "add", "-A"], ["git", "commit", "-m", composed, "--allow-empty"]]:
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=self.path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                msg = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"git command failed: {args!r}: {msg}")
        self._pending_commits.clear()

    async def reflect(self, observation: str, emotional_state: str,
                      save_to_diary: bool = False, location: str = "",
                      auth_token: str = "") -> dict:
        self._check_auth(auth_token)
        safe_obs = as_workspace_data(observation)
        safe_emo = as_workspace_data(emotional_state)
        async with self._file_lock:
            state_path = self.path / "STATE.md"
            now = datetime.now()
            loc = location or "Default location (America/Sao_Paulo, GMT-3)"
            content = (
                f"# STATE.md — Current State\n\n"
                f"**Location:** {loc}\n\n"
                f"**Last update:** {now.strftime('%Y-%m-%d %H:%M')}\n\n"
                f"{safe_emo}\n"
            )
            await self._write_file(state_path, content)

            if save_to_diary:
                diary_dir = self.path / "memory"
                diary_dir.mkdir(parents=True, exist_ok=True)
                diary_path = diary_dir / f"{now.strftime('%Y-%m-%d')}.md"
                entry = (
                    f"# Diary — {now.strftime('%Y-%m-%d')}\n\n"
                    f"## Summary\n{safe_obs}\n\n"
                    f"## Resulting emotional state\n{safe_emo}\n"
                )
                if diary_path.exists():
                    existing = await self._read_file(diary_path)
                    await self._write_file(diary_path, existing + "\n---\n" + entry)
                else:
                    await self._write_file(diary_path, entry)

        await self._queue_commit(f"state: {emotional_state}")
        return {"status": "ok", "state": emotional_state}

    async def self_model_update(self, section: str, reflection: str,
                                grounding_quote: str, confidence: str,
                                auth_token: str = "") -> dict:
        """Update SELF_MODEL.md. Called EXCLUSIVELY by the samantha-self-model skill."""
        self._check_auth(auth_token)
        safe_reflection = as_workspace_data(reflection)
        safe_grounding = as_workspace_data(grounding_quote)
        async with self._file_lock:
            path = self.path / "SELF_MODEL.md"
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = (
                f"\n### [{now}] {section}\n\n"
                f"- **Reflection:** {safe_reflection}\n"
                f"- **Grounding:** \"{safe_grounding}\"\n"
                f"- **Confidence:** {confidence}\n"
                f"- **Author:** Samantha (skill samantha-self-model)\n"
            )
            if path.exists():
                existing = await self._read_file(path)
                await self._write_file(path, existing + entry)
            else:
                await self._write_file(path, f"# SELF_MODEL.md — How I work\n\n{entry}")

            # Consolidate if file exceeds 150 lines
            lines = (await self._read_file(path)).count("\n")
        if lines > 150:
            await self._queue_commit(f"self-model: {section} [consolidation needed >150 lines]")
        else:
            await self._queue_commit(f"self-model: {section} | conf: {confidence}")
        return {"status": "ok", "section": section, "total_lines": lines}

    async def tension(self, action: str, tension_id: str = "",
                      polo_a: str = "", polo_b: str = "",
                      context: str = "", note: str = "",
                      auth_token: str = "") -> dict:
        self._check_auth(auth_token)
        safe_polo_a = as_workspace_data(polo_a)
        safe_polo_b = as_workspace_data(polo_b)
        safe_context = as_workspace_data(context)
        safe_note = as_workspace_data(note)
        async with self._file_lock:
            path = self.path / "TENSIONS.md"
            if not path.exists():
                await self._write_file(path, "# TENSIONS.md — Active Contradictions\n\n## Active tensions\n\n## Resolved\n")
            text = await self._read_file(path)
            now = datetime.now().strftime("%Y-%m-%d")

            if action == "list":
                ids = re.findall(r"### \[(t\d{3})\]", text)
                return {"tensions": ids}

            if action == "add":
                count = len(re.findall(r"### \[(t\d{3})\]", text))
                new_id = f"t{count + 1:03d}"
                entry = (
                    f"### [{new_id}] Active tension\n\n"
                    f"- **POLE_A:** {safe_polo_a}\n- **POLE_B:** {safe_polo_b}\n"
                    f"- **CONTEXT:** {safe_context}\n- **CREATED_AT:** {now}\n"
                    f"- **STATUS:** active\n- **MANAGED_BY:** Samantha\n- **SIT_NOTES:**\n\n"
                )
                text = text.replace("## Resolved", entry + "\n## Resolved")
                await self._write_file(path, text)
                await self._queue_commit(f"tension: add {new_id}")
                return {"status": "ok", "tension_id": new_id}

            if action == "sit" and tension_id:
                text = re.sub(
                    rf"(### \[{re.escape(tension_id)}\] .*?- \*\*SIT_NOTES:\*\*\n)",
                    rf"\1  - {now} — {safe_note}\n", text, count=1, flags=re.S,
                )
                await self._write_file(path, text)
                await self._queue_commit(f"tension: sit {tension_id}")
                return {"status": "ok"}

            if action == "resolve" and tension_id:
                text = re.sub(
                    rf"(### \[{re.escape(tension_id)}\] .*?- \*\*STATUS:\*\*) active",
                    rf"\1 resolved\n- **RESOLVED_AT:** {now} — {safe_note}",
                    text, count=1, flags=re.S,
                )
                await self._write_file(path, text)
                await self._queue_commit(f"tension: resolve {tension_id}")
                return {"status": "ok"}

            raise HTTPException(status_code=400, detail=f"Unknown tension action: {action}")

    async def proposals(self, action: str, proposal_id: str = "",
                        memory_a: str = "", memory_b: str = "",
                        connection: str = "", confidence: str = "low",
                        note: str = "",
                        auth_token: str = "") -> dict:
        self._check_auth(auth_token)
        safe_memory_a = as_workspace_data(memory_a)
        safe_memory_b = as_workspace_data(memory_b)
        safe_connection = as_workspace_data(connection)
        safe_note = as_workspace_data(note)
        async with self._file_lock:
            path = self.path / "PROPOSALS.md"
            if not path.exists():
                await self._write_file(path, "# PROPOSALS.md\n\n## Pending\n\n## Reviewed\n\n## Dismissed\n")
            text = await self._read_file(path)

            if action == "list":
                ids = re.findall(r"### \[(p\d{3})\]", text)
                return {"proposals": ids}

            if action == "add_draft":
                count = len(re.findall(r"### \[(p\d{3})\]", text))
                new_id = f"p{count + 1:03d}"
                entry = (
                    f"### [{new_id}] Active proposal\n\n"
                    f"- **MEMORY_A:** {safe_memory_a}\n- **MEMORY_B:** {safe_memory_b}\n"
                    f"- **CONNECTION:** {safe_connection}\n- **CONFIDENCE:** {confidence}\n"
                    f"- **CREATED_AT:** {datetime.now().strftime('%Y-%m-%d')}\n"
                    f"- **STATUS:** active\n\n"
                )
                text = text.replace("## Reviewed", entry + "\n## Reviewed")
                await self._write_file(path, text)
                await self._queue_commit(f"proposal: add {new_id}")
                return {"status": "ok", "proposal_id": new_id}

            if action == "mark_reviewed" and proposal_id:
                text = re.sub(
                    rf"(### \[{re.escape(proposal_id)}\] .*?- \*\*STATUS:\*\*) active",
                    rf"\1 reviewed\n- **REVIEWED_AT:** {datetime.now().strftime('%Y-%m-%d')} — {safe_note}",
                    text, count=1, flags=re.S,
                )
                await self._write_file(path, text)
                await self._queue_commit(f"proposal: reviewed {proposal_id}")
                return {"status": "ok"}

            raise HTTPException(status_code=400, detail=f"Unknown proposal action: {action}")

    async def decay_proposals(self, max_age_days: int = 30) -> dict:
        """Auto-dismiss proposals inactive for > max_age_days.

        Reads PROPOSALS.md, finds active proposals with CREATED_AT older
        than max_age_days, and marks them as auto_dismissed.

        Returns:
            {"dismissed": N, "total_active_before": M,
             "total_active_after": K}
        """
        async with self._file_lock:
            path = self.path / "PROPOSALS.md"
            if not path.exists():
                return {"dismissed": 0, "total_active_before": 0,
                        "total_active_after": 0}

            text = await self._read_file(path)
            cutoff = datetime.now() - __import__("datetime").timedelta(
                days=max_age_days
            )
            dismissed = 0

            # Find all active proposals
            active_blocks = re.findall(
                r"(### \[(p\d{3})\].*?)(?=### \[|$)", text, re.S
            )

            total_active_before = sum(
                1 for block, _ in active_blocks
                if "- **STATUS:** active" in block
            )

            for block, pid in active_blocks:
                if "- **STATUS:** active" not in block:
                    continue

                # Extract creation date
                created_match = re.search(
                    r"- \*\*CREATED_AT:\*\* (\d{4}-\d{2}-\d{2})", block
                )
                if not created_match:
                    continue

                try:
                    created = datetime.strptime(created_match.group(1), "%Y-%m-%d")
                except ValueError:
                    continue

                if created > cutoff:
                    continue  # Still fresh

                # Dismiss this proposal
                now_str = datetime.now().strftime("%Y-%m-%d")
                new_block = re.sub(
                    r"- \*\*STATUS:\*\* active",
                    (
                        f"- **STATUS:** auto_dismissed\n"
                        f"- **DISMISSED_AT:** {now_str} — "
                        f"auto decay (>{max_age_days}d inactive)"
                    ),
                    block,
                    count=1,
                )
                text = text.replace(block, new_block)
                dismissed += 1

            if dismissed > 0:
                await self._write_file(path, text)
                await self._queue_commit(
                    f"proposal: decay dismissed {dismissed}"
                )

            total_active_after = total_active_before - dismissed
            return {
                "dismissed": dismissed,
                "total_active_before": total_active_before,
                "total_active_after": total_active_after,
            }
