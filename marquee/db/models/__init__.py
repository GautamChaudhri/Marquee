"""The ORM models. Importing this package registers every table on ``Base.metadata``."""

from __future__ import annotations

from marquee.db.models.artwork import (
    ARTWORK_POSTER,
    ORIGIN_LIBRARY,
    ORIGIN_MARQUEE,
    SUBJECT_MOVIE,
    Artwork,
)
from marquee.db.models.deployment import Deployment
from marquee.db.models.evaluation import Evaluation, EvaluationResult
from marquee.db.models.job import Job, JobEvent, JobSchedule
from marquee.db.models.movie import Movie
from marquee.db.models.residual import ResidualTraining
from marquee.db.models.run import PosterRun, RunCandidate
from marquee.db.models.taste import Exemplar, PreferenceEvent, TasteProfile

__all__ = [
    "ARTWORK_POSTER",
    "ORIGIN_LIBRARY",
    "ORIGIN_MARQUEE",
    "SUBJECT_MOVIE",
    "Artwork",
    "Deployment",
    "Evaluation",
    "EvaluationResult",
    "Exemplar",
    "Job",
    "JobEvent",
    "JobSchedule",
    "Movie",
    "PosterRun",
    "PreferenceEvent",
    "ResidualTraining",
    "RunCandidate",
    "TasteProfile",
]
