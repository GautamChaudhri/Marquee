"""Authoritative immutable JMC2B job-definition registry."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from marquee.core.jobs.contracts import (
    EffectSafety,
    ExecutionClass,
    FeatureArea,
    MigrationState,
    TriggerKind,
)
from marquee.core.jobs.documents import DocumentAdapter
from marquee.core.jobs.safety_gates import SafetyPolicy

_JOB_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_POLICY_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")


class JobDefinitionError(RuntimeError):
    """Base error for invalid registry construction or lookup."""


class DuplicateJobDefinitionError(JobDefinitionError):
    pass


class UnknownJobDefinitionError(JobDefinitionError, LookupError):
    pass


class InvalidJobDefinitionError(JobDefinitionError):
    pass


class DisabledJobDefinitionError(JobDefinitionError):
    pass


@dataclass(frozen=True)
class TimeoutPolicy:
    seconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.seconds <= 7 * 24 * 60 * 60:
            raise ValueError("timeout must be between one second and seven days")


@dataclass(frozen=True)
class JobDefinition:
    job_type: str
    label_key: str
    feature_area: FeatureArea
    presentation_family: str
    presenter_key: str
    enabled: bool
    migration_state: MigrationState
    disabled_reason: str | None
    request: DocumentAdapter
    result: DocumentAdapter
    error: DocumentAdapter
    execution_class: ExecutionClass
    entrypoint: str
    timeout: TimeoutPolicy
    effect_safety: EffectSafety
    safety_policy: SafetyPolicy = field(default_factory=SafetyPolicy)
    configuration_keys: frozenset[str] = field(default_factory=frozenset)
    subject_builder: Callable[..., Any] | None = None
    progress_policy: Any = None
    retry_policy: Any = None
    action_policy: Any = None
    parent_policy: Any = None
    trigger_kinds: frozenset[TriggerKind] = field(default_factory=frozenset)
    child_job_types: frozenset[str] = field(default_factory=frozenset)
    subject_kinds: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration_keys", frozenset(self.configuration_keys))
        object.__setattr__(self, "trigger_kinds", frozenset(self.trigger_kinds))
        object.__setattr__(self, "child_job_types", frozenset(self.child_job_types))
        object.__setattr__(self, "subject_kinds", frozenset(self.subject_kinds))


class JobDefinitionRegistry:
    """Sole bounded authority for built-in definition data and policies."""

    def __init__(self, definitions: Iterable[JobDefinition] = ()) -> None:
        items: dict[str, JobDefinition] = {}
        for definition in definitions:
            self._validate_definition(definition)
            if definition.job_type in items:
                raise DuplicateJobDefinitionError(definition.job_type)
            items[definition.job_type] = definition
        self._definitions = MappingProxyType(items)

    @staticmethod
    def _validate_definition(definition: JobDefinition) -> None:
        if not _JOB_TYPE.fullmatch(definition.job_type):
            raise InvalidJobDefinitionError(f"invalid canonical job type: {definition.job_type}")
        for value, field_name in (
            (definition.label_key, "label_key"),
            (definition.presentation_family, "presentation_family"),
            (definition.presenter_key, "presenter_key"),
            (definition.entrypoint, "entrypoint"),
        ):
            if not _POLICY_KEY.fullmatch(value):
                raise InvalidJobDefinitionError(f"invalid {field_name} for {definition.job_type}")
        if definition.presenter_key in {"generic", "default", "unknown"}:
            raise InvalidJobDefinitionError("built-ins require a non-generic presenter key")
        if definition.enabled:
            if definition.job_type != "system_noop":
                raise InvalidJobDefinitionError("only system_noop may be dispatch-enabled")
            if definition.execution_class != ExecutionClass.CONTROL:
                raise InvalidJobDefinitionError("system_noop must use the control execution class")
        elif not definition.disabled_reason:
            raise InvalidJobDefinitionError("disabled definitions require a reason")
        if definition.migration_state == MigrationState.PARENT_ONLY and definition.enabled:
            raise InvalidJobDefinitionError("parent-only definitions cannot dispatch")
        if len(definition.configuration_keys) > 128:
            raise InvalidJobDefinitionError("configuration dependency set is unbounded")
        if any(not key or len(key) > 100 for key in definition.configuration_keys):
            raise InvalidJobDefinitionError("invalid configuration dependency key")

    def get(self, job_type: str) -> JobDefinition:
        try:
            return self._definitions[job_type]
        except KeyError as exc:
            raise UnknownJobDefinitionError(job_type) from exc

    def find(self, job_type: str) -> JobDefinition | None:
        return self._definitions.get(job_type)

    def __iter__(self):
        return iter(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)

    @property
    def types(self) -> frozenset[str]:
        return frozenset(self._definitions)

    @property
    def enabled_types(self) -> frozenset[str]:
        return frozenset(
            definition.job_type for definition in self if definition.enabled
        )

    def validate_coverage(self, expected_types: Iterable[str]) -> None:
        expected = frozenset(expected_types)
        missing = expected - self.types
        unexpected = self.types - expected
        if missing or unexpected:
            raise InvalidJobDefinitionError(
                f"definition coverage mismatch; missing={sorted(missing)}, "
                f"unexpected={sorted(unexpected)}"
            )

    def validate_complete(self, expected_types: Iterable[str]) -> None:
        """Certify the complete built-in matrix without weakening small test registries."""
        self.validate_coverage(expected_types)
        for definition in self:
            missing = [
                name
                for name in ("subject_builder", "progress_policy", "retry_policy", "action_policy")
                if getattr(definition, name) is None
            ]
            if missing:
                raise InvalidJobDefinitionError(
                    f"{definition.job_type} has incomplete policies: {', '.join(missing)}"
                )
            if not definition.trigger_kinds:
                raise InvalidJobDefinitionError(
                    f"{definition.job_type} requires at least one trigger kind"
                )
            if not definition.subject_kinds:
                raise InvalidJobDefinitionError(
                    f"{definition.job_type} requires explicit subject kinds"
                )
            if TriggerKind.WEBHOOK in definition.trigger_kinds and definition.enabled:
                raise InvalidJobDefinitionError("webhook definitions must remain disabled")
            try:
                definition.retry_policy.validate_safety(definition.effect_safety)
            except ValueError as exc:
                raise InvalidJobDefinitionError(
                    f"unsafe retry policy for {definition.job_type}: {exc}"
                ) from exc
            parent_only = definition.migration_state == MigrationState.PARENT_ONLY
            if parent_only != (definition.parent_policy is not None):
                raise InvalidJobDefinitionError(
                    f"{definition.job_type} parent policy does not match migration state"
                )
            unknown_children = definition.child_job_types - self.types
            if unknown_children:
                raise InvalidJobDefinitionError(
                    f"{definition.job_type} has unknown children: {sorted(unknown_children)}"
                )

    def for_dispatch(self, job_type: str, *, entrypoint: str) -> JobDefinition:
        definition = self.get(job_type)
        if not definition.enabled:
            raise DisabledJobDefinitionError(
                f"{job_type} is defined but dispatch-disabled: {definition.disabled_reason}"
            )
        if definition.entrypoint != entrypoint:
            raise DisabledJobDefinitionError(
                f"{job_type} is not enabled for entrypoint {entrypoint}"
            )
        return definition
