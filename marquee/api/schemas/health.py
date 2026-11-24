"""Health responses. They never carry a host name, a path, or a version."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Live(BaseModel):
    status: Literal["ok"] = "ok"


class Ready(BaseModel):
    status: Literal["ready", "not ready"]
    reason: str | None = None
