"""SQLAlchemy ORM models — Movie, Series, Season, Episode, runs + events."""

from marquee.models.artwork_event import ArtworkEvent
from marquee.models.base import ArtworkMixin, TimestampMixin
from marquee.models.dovi import DoviState
from marquee.models.episode import Episode
from marquee.models.job import (
    Job,
    JobAttempt,
    JobEvent,
    JobResource,
    JobResourceReservation,
    JobSchedule,
    JobWorker,
)
from marquee.models.letterbox import LetterboxEvent, LetterboxState
from marquee.models.letterbox_reencode import LetterboxReencodeArtifact
from marquee.models.media_backup import MediaBackup
from marquee.models.media_file import EpisodeMediaFile, MediaFile
from marquee.models.media_job import MediaBatch, MediaJob, MediaJobEvent
from marquee.models.movie import Movie
from marquee.models.pipeline_run import PipelineRun
from marquee.models.radarr_overlay import (
    MovieCustomFormatScore,
    RadarrCustomFormat,
    RadarrOverlayProfilePreference,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
)
from marquee.models.season import Season
from marquee.models.series import Series
from marquee.models.subtitle_inventory import SubtitleInventory, SubtitleTrack
from marquee.models.subtitle_managed import (
    ManagedSubtitleAsset,
    ManagedSubtitleBinding,
)
from marquee.models.subtitle_policy import SubtitlePolicy, SubtitlePolicyBinding
from marquee.models.system_metrics import SystemMetricsSample

__all__ = [
    "ArtworkEvent",
    "ArtworkMixin",
    "TimestampMixin",
    "DoviState",
    "LetterboxEvent",
    "LetterboxState",
    "LetterboxReencodeArtifact",
    "Job",
    "JobAttempt",
    "JobEvent",
    "JobResource",
    "JobResourceReservation",
    "JobSchedule",
    "JobWorker",
    "EpisodeMediaFile",
    "MediaFile",
    "MediaBatch",
    "MediaJob",
    "MediaJobEvent",
    "MediaBackup",
    "SubtitleInventory",
    "SubtitleTrack",
    "ManagedSubtitleAsset",
    "ManagedSubtitleBinding",
    "SubtitlePolicy",
    "SubtitlePolicyBinding",
    "SystemMetricsSample",
    "Movie",
    "PipelineRun",
    "RadarrCustomFormat",
    "RadarrQualityProfile",
    "RadarrProfileFormatItem",
    "MovieCustomFormatScore",
    "RadarrOverlayProfilePreference",
    "Season",
    "Series",
    "Episode",
]
