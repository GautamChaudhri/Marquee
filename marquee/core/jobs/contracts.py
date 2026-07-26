"""Stable JMC2B job-definition taxonomy.

Serialized enum values are product contracts.  Additions require an explicit
schema/API change; callers must never coerce unknown strings.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionClass(StrEnum):
    CONTROL = "control"
    NETWORK = "network"
    CPU = "cpu"
    MEDIA_READ = "media_read"
    MEDIA_WRITE = "media_write"
    GPU = "gpu"
    MAINTENANCE = "maintenance"


class FeatureArea(StrEnum):
    AI_POSTERS = "ai_posters"
    LIBRARY_INTEGRATIONS = "library_integrations"
    ML_TASTE = "ml_taste"
    MAINTENANCE = "maintenance"
    SYSTEM = "system"


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    POLICY = "policy"
    BATCH = "batch"
    PARENT = "parent"
    HEALING = "healing"
    SYSTEM = "system"
    WEBHOOK = "webhook"


class EffectSafety(StrEnum):
    READ_ONLY = "read_only"
    STAGED_IDEMPOTENT = "staged_idempotent"
    UNSAFE_MUTATION = "unsafe_mutation"


class ProgressStrategy(StrEnum):
    DETERMINATE = "determinate"
    INDETERMINATE = "indeterminate"
    HYBRID = "hybrid"
    NONE = "none"


class AttentionLevel(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    ERROR = "error"


class AttentionReason(StrEnum):
    NONE = "none"
    WAITING = "waiting"
    HELD = "held"
    RETRYING = "retrying"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    UNSAFE = "unsafe"


class JobAction(StrEnum):
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    CHANGE_PRIORITY = "change_priority"
    RETRY = "retry"
    OPEN_LOGS = "open_logs"
    OPEN_ARTIFACTS = "open_artifacts"
    OPEN_DETAIL = "open_detail"


class MigrationState(StrEnum):
    ENABLED = "enabled"
    DEFINED_DISABLED = "defined_disabled"
    PARENT_ONLY = "parent_only"

