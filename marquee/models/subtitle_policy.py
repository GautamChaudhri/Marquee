"""Subtitle language policies + scope bindings (design §21).

A policy is allowlist/blocklist over normalized language tags, with conservative
protections (forced, default, last full-dialogue) and audit-only-by-default
semantics. ``revision`` is stamped into every plan so a mid-flight policy edit
invalidates stale plans. Bindings resolve most-specific-first:
media-file/item → series → library (movies/TV) → global.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marquee.database import Base


class SubtitlePolicy(Base):
    """A language-cleanup policy."""

    __tablename__ = "subtitle_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # allowlist | blocklist
    mode: Mapped[str] = mapped_column(String(12), nullable=False, default="blocklist")
    languages_json: Mapped[str | None] = mapped_column(JSON, nullable=True)

    # keep | review | remove
    unknown_action: Mapped[str] = mapped_column(String(10), nullable=False, default="keep")
    protect_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    protect_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    protect_last_full_dialogue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_external: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_apply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    audit_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # block | allow_break
    hardlink_action: Mapped[str] = mapped_column(String(12), nullable=False, default="block")
    # none | keep_original
    backup_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<SubtitlePolicy(id={self.id}, name={self.name!r}, mode={self.mode!r})>"


class SubtitlePolicyBinding(Base):
    """Binds a policy to a scope (global / movies / tv / series / item)."""

    __tablename__ = "subtitle_policy_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("subtitle_policies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # global | movies | tv | series | movie | episode | media_file
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SubtitlePolicyBinding(policy_id={self.policy_id}, "
            f"scope={self.scope_type}:{self.scope_id})>"
        )
