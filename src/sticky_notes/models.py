from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Note:
    id: int | None
    title: str
    body_html: str
    color: str
    tags: str
    is_pinned: bool
    is_priority: bool
    reminder_minutes: int
    reminder_start_at: str
    sort_order: int
    created_at: str
    updated_at: str
    deleted_at: str
