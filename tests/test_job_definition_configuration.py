from __future__ import annotations

from dataclasses import replace

import pytest

from marquee.core.configuration import ConfigurationError
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.snapshotting import snapshot_definition_configuration
from tests.test_job_definition_documents import _definition


@pytest.fixture
async def config_provider(db):
    """The configuration provider with a valid revision.

    Depending on ``db`` (which loads the initial configuration revision) keeps
    these snapshot tests independent of collection order instead of relying on
    another test having initialized the module-global provider first.
    """
    return configuration_provider


async def test_definition_snapshots_only_declared_configuration_keys(config_provider) -> None:
    definition = replace(
        _definition(),
        configuration_keys=frozenset({"OCR_CONFIDENCE_THRESHOLD", "OCR_MAX_RESIDUAL_BOXES"}),
    )
    snapshot = snapshot_definition_configuration(definition, config_provider)
    assert set(snapshot.values) == definition.configuration_keys
    assert snapshot.version == config_provider.state.version


async def test_definition_configuration_rejects_secret_or_restart_keys(config_provider) -> None:
    definition = replace(_definition(), configuration_keys=frozenset({"SUBGEN_CALLBACK_TOKEN"}))
    with pytest.raises(ConfigurationError, match="cannot enter a job snapshot"):
        snapshot_definition_configuration(definition, config_provider)
