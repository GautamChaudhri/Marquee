"""Definition-owned enqueue snapshot helpers."""

from __future__ import annotations

from typing import Protocol

from marquee.core.configuration_cache import ExecutionConfigurationSnapshot
from marquee.core.jobs.definitions import JobDefinition


class ConfigurationSnapshotProvider(Protocol):
    def snapshot_for(self, keys: frozenset[str]) -> ExecutionConfigurationSnapshot: ...


def snapshot_definition_configuration(
    definition: JobDefinition,
    provider: ConfigurationSnapshotProvider,
) -> ExecutionConfigurationSnapshot:
    """Snapshot only the definition's bounded, validated JMC2A dependencies."""
    return provider.snapshot_for(definition.configuration_keys)
