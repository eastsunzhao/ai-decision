"""Shared types for scope-level data querying."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryRequest:
    scope: str
    argv: list[str] = field(default_factory=list)


@dataclass
class QueryResult:
    scope: str
    exit_code: int
    payload: Any = None
