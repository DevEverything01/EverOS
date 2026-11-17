"""Filesystem-side aggregates for the console dashboard.

The md-first store is the source of truth for "what memory exists", while
``control.db`` holds request and usage counters. This module reads the store
read-only: it never mutates files and never opens the sqlite index, so it is
safe to call while extraction is running.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ENTRY_RE = re.compile(r"<!-- entry:([A-Za-z0-9_]+) -->")
_PROFILE_ITEM_RE = re.compile(r"^- description:", re.MULTILINE)
_DAY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class StoreStats:
    """Aggregated counts and a per-day activity series."""

    profiles: int = 0
    profile_items: int = 0
    episodes: int = 0
    cases: int = 0
    skills: int = 0
    agents: int = 0
    knowledge_docs: int = 0
    storage_bytes: int = 0
    users: list[dict] = field(default_factory=list)
    activity: dict[str, dict[str, int]] = field(default_factory=dict)

    def bump(self, day: str, kind: str, amount: int = 1) -> None:
        bucket = self.activity.setdefault(day, {})
        bucket[kind] = bucket.get(kind, 0) + amount


def _project_dir(root: Path) -> Path:
    return root / "default_app" / "default_project"


def scan_store(root: Path) -> StoreStats:
    """Walk the md store and return counts plus a per-day activity series."""
    stats = StoreStats()
    project = _project_dir(root)

    users_dir = project / "users"
    if users_dir.is_dir():
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            profile_md = user_dir / "user.md"
            items = 0
            if profile_md.is_file():
                items = len(_PROFILE_ITEM_RE.findall(_read(profile_md)))
            stats.profiles += 1
            stats.profile_items += items

            episodes = 0
            ep_dir = user_dir / "episodes"
            if ep_dir.is_dir():
                for md in ep_dir.glob("*.md"):
                    text = _read(md)
                    count = len(_ENTRY_RE.findall(text))
                    episodes += count
                    day = _day_of(md, text)
                    if day:
                        stats.bump(day, "episodes", count)
            stats.episodes += episodes
            stats.users.append(
                {
                    "id": user_dir.name,
                    "profiles": items,
                    "episodes": episodes,
                    "last": _last_entry_ts(user_dir),
                }
            )

    agents_dir = project / "agents"
    if agents_dir.is_dir():
        for agent_dir in sorted(p for p in agents_dir.iterdir() if p.is_dir()):
            stats.agents += 1
            skills_dir = agent_dir / "skills"
            if skills_dir.is_dir():
                for skill in skills_dir.iterdir():
                    if skill.is_dir() and (skill / "SKILL.md").is_file():
                        stats.skills += 1
            cases_dir = agent_dir / ".cases"
            if cases_dir.is_dir():
                for md in cases_dir.glob("*.md"):
                    text = _read(md)
                    count = len(_ENTRY_RE.findall(text))
                    stats.cases += count
                    day = _day_of(md, text)
                    if day:
                        stats.bump(day, "cases", count)

    knowledge_dir = root / "knowledge"
    if knowledge_dir.is_dir():
        stats.knowledge_docs = sum(1 for _ in knowledge_dir.rglob("*.md"))

    stats.storage_bytes = _dir_size(root)
    stats.users.sort(key=lambda u: u["last"] or "", reverse=True)
    return stats


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _day_of(path: Path, text: str) -> str | None:
    match = _DAY_RE.search(path.name)
    if match:
        return match.group(1)
    match = _DAY_RE.search(text[:400])
    return match.group(1) if match else None


def _last_entry_ts(user_dir: Path) -> str:
    """Latest ``**timestamp**:`` value across a user's episode files."""
    latest = ""
    ep_dir = user_dir / "episodes"
    if not ep_dir.is_dir():
        return latest
    for md in ep_dir.glob("*.md"):
        for line in _read(md).splitlines():
            if line.startswith("**timestamp**:"):
                value = line.split(":", 1)[1].strip()
                latest = max(latest, value)
    return latest


def _dir_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total
