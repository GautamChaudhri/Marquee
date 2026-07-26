"""Presenter registry keyed by the manifest's unique presenter keys.

Every built-in definition resolves to a dedicated presenter.  The deliberately
labelled generic presenter exists only for truly unknown historical/external
job types; resolution for a registered built-in never falls back to it, and
coverage tests keep that path unreachable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.presentation import JobPresentation, JobRow
from marquee.core.jobs.presenters.base import (
    JobPresenter,
    PresentationIntegrityError,
    PresenterContext,
    load_context,
)
from marquee.core.jobs.presenters.posters import POSTER_JOB_TYPES, PosterPresenter
from marquee.core.jobs.presenters.supporting import build_supporting_presenters

if TYPE_CHECKING:
    from marquee.models import Job

__all__ = [
    "GENERIC_PRESENTER",
    "JOB_PRESENTER_REGISTRY",
    "GenericJobPresenter",
    "JobPresenter",
    "PresentationIntegrityError",
    "PresenterContext",
    "UnregisteredPresenterError",
    "load_context",
    "present_job",
    "present_job_row",
    "resolve_presenter",
]


class UnregisteredPresenterError(RuntimeError):
    """A registered built-in definition has no presenter — a coverage defect."""


class GenericJobPresenter(JobPresenter):
    """Deliberately labelled fallback for unknown historical/external types only."""

    generic = True

    def __init__(self) -> None:
        super().__init__("unknown")
        self.key = "jobs.generic_unknown"


GENERIC_PRESENTER = GenericJobPresenter()


def _build_registry() -> Mapping[str, JobPresenter]:
    presenters: dict[str, JobPresenter] = {}
    for job_type in POSTER_JOB_TYPES:
        presenters[f"jobs.{job_type}"] = PosterPresenter(job_type)
    presenters.update(build_supporting_presenters())
    return presenters


JOB_PRESENTER_REGISTRY: Mapping[str, JobPresenter] = _build_registry()


def resolve_presenter(definition: JobDefinition) -> JobPresenter:
    presenter = JOB_PRESENTER_REGISTRY.get(definition.presenter_key)
    if presenter is None:
        raise UnregisteredPresenterError(
            f"definition {definition.job_type} has no presenter for key "
            f"{definition.presenter_key}"
        )
    return presenter


def present_job(job: Job, definition: JobDefinition, **context_kwargs) -> JobPresentation:
    presenter = resolve_presenter(definition)
    return presenter.present(load_context(job, definition, **context_kwargs))


def present_job_row(job: Job, definition: JobDefinition, **context_kwargs) -> JobRow:
    presenter = resolve_presenter(definition)
    return presenter.present_row(load_context(job, definition, **context_kwargs))
