"""Complete immutable manifest for every inventoried built-in job type."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from marquee.core.jobs.contracts import (
    EffectSafety,
    ExecutionClass,
    FeatureArea,
    MigrationState,
    ProgressStrategy,
    TriggerKind,
)
from marquee.core.jobs.definitions import (
    JobDefinition,
    JobDefinitionRegistry,
    TimeoutPolicy,
)
from marquee.core.jobs.documents import (
    BuiltInIntentV1,
    BuiltInResultV1,
    DocumentKind,
    DoviAnalyzeRequestV1,
    LearnedHeadTrainRequestV1,
    LetterboxDetectEpisodeRequestV1,
    LetterboxDetectRequestV1,
    LetterboxDetectTvScopeRequestV1,
    LibrarySyncRequestV1,
    MlPublicationResultV1,
    PosterBatchRequestV1,
    PosterPipelineRequestV1,
    PosterPipelineResultV1,
    PosterRescanRequestV1,
    PosterRescanResultV1,
    SafeJobErrorV1,
    StrictDocument,
    SubtitlePolicyAuditRequestV1,
    SubtitleScanRequestV1,
    SystemNoopRequestV1,
    TasteMapRequestV1,
    TasteRebuildRequestV1,
    current_adapter,
)
from marquee.core.jobs.inventory import (
    BUILTIN_JOB_TYPES,
    HEALING_TYPES,
    PARENT_ONLY_TYPES,
    SCHEDULE_PRODUCED_TYPES,
    WEBHOOK_RESERVED_TYPES,
)
from marquee.core.jobs.policies import ActionPolicy, ParentAggregationPolicy, RetryPolicy
from marquee.core.jobs.progress import ProgressPolicy
from marquee.core.jobs.subjects import SUBJECT_SNAPSHOT_ADAPTER, SubjectSnapshot


@dataclass(frozen=True)
class _DefinitionSpec:
    job_type: str
    feature: FeatureArea
    execution: ExecutionClass
    safety: EffectSafety
    subject_kinds: frozenset[str]
    progress: ProgressStrategy = ProgressStrategy.HYBRID
    child_job_types: frozenset[str] = frozenset()


def _spec(
    job_type: str,
    feature: FeatureArea,
    execution: ExecutionClass,
    safety: EffectSafety,
    *subject_kinds: str,
    progress: ProgressStrategy = ProgressStrategy.HYBRID,
    children: tuple[str, ...] = (),
) -> _DefinitionSpec:
    return _DefinitionSpec(
        job_type=job_type,
        feature=feature,
        execution=execution,
        safety=safety,
        subject_kinds=frozenset(subject_kinds),
        progress=progress,
        child_job_types=frozenset(children),
    )


_R = EffectSafety.READ_ONLY
_U = EffectSafety.UNSAFE_MUTATION

_SPECS = (
    _spec("system_noop", FeatureArea.SYSTEM, ExecutionClass.CONTROL, _R, "system_work", progress=ProgressStrategy.NONE),
    _spec("poster_heal", FeatureArea.AI_POSTERS, ExecutionClass.NETWORK, _U, "movie", "series", "season"),
    _spec("letterbox_heal", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_WRITE, _U, "media_file"),
    _spec("letterbox_detect", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_READ, _R, "movie", "media_file"),
    _spec("letterbox_detect_episode", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_READ, _R, "episode", "media_file"),
    _spec("letterbox_detect_tv_scope", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_READ, _R, "series", "season", "episode"),
    _spec("letterbox_apply", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_WRITE, _U, "movie", "media_file"),
    _spec("letterbox_remove", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_WRITE, _U, "movie", "media_file"),
    _spec("letterbox_apply_tv_scope", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_WRITE, _U, "series", "season", "episode"),
    _spec("letterbox_revert_tv_scope", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_WRITE, _U, "series", "season", "episode"),
    _spec("backup_create", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("taste_rebuild", FeatureArea.ML_TASTE, ExecutionClass.GPU, _R, "model_profile_training"),
    _spec("taste_map", FeatureArea.ML_TASTE, ExecutionClass.CPU, _R, "model_profile_training"),
    _spec("library_sync", FeatureArea.LIBRARY_INTEGRATIONS, ExecutionClass.NETWORK, _R, "maintenance_scope"),
    _spec("subtitle_scan_all", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("subtitle_scan",)),
    _spec("audio_subs_deep_scan", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("subtitle_scan",)),
    _spec("radarr_upgrade", FeatureArea.LIBRARY_INTEGRATIONS, ExecutionClass.NETWORK, _R, "movie"),
    _spec("poster_pipeline", FeatureArea.AI_POSTERS, ExecutionClass.GPU, _R, "movie", "series", "season", "episode"),
    _spec("poster_pipeline_batch", FeatureArea.AI_POSTERS, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("poster_pipeline",)),
    _spec("poster_pipeline_tv_batch", FeatureArea.AI_POSTERS, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("poster_pipeline",)),
    _spec("learned_head_train", FeatureArea.ML_TASTE, ExecutionClass.CPU, _R, "model_profile_training"),
    _spec("pipeline_cache_clear", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("poster_deploy_reset", FeatureArea.AI_POSTERS, ExecutionClass.NETWORK, _U, "maintenance_scope"),
    _spec("poster_rescan", FeatureArea.AI_POSTERS, ExecutionClass.MEDIA_READ, _R, "poster_candidate_set"),
    _spec("poster_backup_all", FeatureArea.AI_POSTERS, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("poster_maintenance", FeatureArea.AI_POSTERS, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("job_retention_purge", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("system_metrics_purge", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("dovi_analyze", FeatureArea.HDR, ExecutionClass.MEDIA_READ, _R, "media_file", "movie", "episode"),
    _spec("dovi_convert", FeatureArea.HDR, ExecutionClass.MEDIA_WRITE, _U, "media_file", "movie", "episode"),
    _spec("subtitle_scan", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_READ, _R, "media_file"),
    _spec("subtitle_policy_audit", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.CPU, _R, "maintenance_scope"),
    _spec("audio_remove", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track"),
    _spec("track_remove", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track"),
    _spec("subtitle_remove", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track"),
    _spec("subtitle_embed", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track", "media_file"),
    _spec("subtitle_metadata", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track"),
    _spec("audio_reorder", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track", "media_file"),
    _spec("subtitle_extract", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track"),
    _spec("subtitle_generate", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "media_file", "episode"),
    _spec("subtitle_policy", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track", "media_file"),
    _spec("subtitle_restore", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_WRITE, _U, "track", "media_file"),
    _spec("letterbox_reencode", FeatureArea.LETTERBOX, ExecutionClass.MEDIA_WRITE, _U, "media_file", "movie", "episode"),
    _spec("subtitle_generate_batch", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("subtitle_generate",)),
    _spec("dovi_analyze_batch", FeatureArea.HDR, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("dovi_analyze",)),
    _spec("letterbox_detect_tv_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_detect_tv_scope",)),
    _spec("letterbox_detect_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_detect",)),
    _spec("letterbox_apply_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_apply",)),
    _spec("letterbox_reencode_tv_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_reencode",)),
)

_BATCH_CHILD_TYPES = frozenset(
    child_job_type for spec in _SPECS for child_job_type in spec.child_job_types
)

# Certified dispatch-enabled allowlist. Grows one non-mutating family per JMC4B phase; every
# entry must be read-only and never media_write (enforced by JobDefinitionRegistry). Mutating
# and parent-only definitions are never listed here.
ENABLED_JOB_TYPES: frozenset[str] = frozenset(
    {
        "system_noop",
        "library_sync",
        "subtitle_scan",
        "subtitle_policy_audit",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "dovi_analyze",
        "poster_pipeline",
        "poster_rescan",
        "taste_rebuild",
        "taste_map",
        "learned_head_train",
    }
)

# Per-type request document models. Types absent here fall back to the generic BuiltInIntentV1
# (or the tiny SystemNoopRequestV1 for system_noop). Results stay generic BuiltInResultV1.
_REQUEST_MODELS: dict[str, type[StrictDocument]] = {
    "library_sync": LibrarySyncRequestV1,
    "subtitle_scan": SubtitleScanRequestV1,
    "subtitle_policy_audit": SubtitlePolicyAuditRequestV1,
    "letterbox_detect": LetterboxDetectRequestV1,
    "letterbox_detect_episode": LetterboxDetectEpisodeRequestV1,
    "letterbox_detect_tv_scope": LetterboxDetectTvScopeRequestV1,
    "dovi_analyze": DoviAnalyzeRequestV1,
    "poster_pipeline": PosterPipelineRequestV1,
    "poster_pipeline_batch": PosterBatchRequestV1,
    "poster_pipeline_tv_batch": PosterBatchRequestV1,
    "taste_rebuild": TasteRebuildRequestV1,
    "taste_map": TasteMapRequestV1,
    "learned_head_train": LearnedHeadTrainRequestV1,
    "poster_rescan": PosterRescanRequestV1,
}

_RESULT_MODELS: dict[str, type[StrictDocument]] = {
    "poster_pipeline": PosterPipelineResultV1,
    "poster_rescan": PosterRescanResultV1,
    "taste_rebuild": MlPublicationResultV1,
    "taste_map": MlPublicationResultV1,
    "learned_head_train": MlPublicationResultV1,
}

# Library synchronization has no reliable upstream item/page total, so it narrates honest
# indeterminate phase stages (plan §4/B11) rather than a fabricated percentage.
_LIBRARY_SYNC_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.INDETERMINATE,
    overall_unit="phases",
    denominator_source="none",
    current_unit="phase",
    aggregation_strategy="none",
    stages=(
        ("connecting", "jobs.library_sync.progress.connecting"),
        ("sync_movies", "jobs.library_sync.progress.sync_movies"),
        ("sync_series", "jobs.library_sync.progress.sync_series"),
        ("finalizing", "jobs.library_sync.progress.finalizing"),
    ),
    tool_adapter=None,
    persistence_cadence_seconds=2,
    meaningful_delta_percent=None,
    max_snapshot_staleness_seconds=15,
    eta_capability=False,
)

# A single media-file probe has no reliable up-front total, so it narrates honest indeterminate
# probe/inventory stages rather than a fabricated percentage.
_SUBTITLE_SCAN_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.INDETERMINATE,
    overall_unit="steps",
    denominator_source="none",
    current_unit="step",
    aggregation_strategy="none",
    stages=(
        ("probing", "jobs.subtitle_scan.progress.probing"),
        ("inventorying", "jobs.subtitle_scan.progress.inventorying"),
    ),
    tool_adapter=None,
    persistence_cadence_seconds=2,
    meaningful_delta_percent=None,
    max_snapshot_staleness_seconds=15,
    eta_capability=False,
)

_SUBTITLE_POLICY_AUDIT_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.INDETERMINATE,
    overall_unit="subjects",
    denominator_source="none",
    current_unit="subject",
    aggregation_strategy="none",
    stages=(
        ("selecting", "jobs.subtitle_policy_audit.progress.selecting"),
        ("evaluating", "jobs.subtitle_policy_audit.progress.evaluating"),
    ),
    tool_adapter=None,
    persistence_cadence_seconds=2,
    meaningful_delta_percent=None,
    max_snapshot_staleness_seconds=15,
    eta_capability=False,
)

_LETTERBOX_DETECT_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.INDETERMINATE,
    overall_unit="samples",
    denominator_source="none",
    current_unit="sample",
    aggregation_strategy="none",
    stages=(
        ("probing", "jobs.letterbox_detect.progress.probing"),
        ("sampling", "jobs.letterbox_detect.progress.sampling"),
        ("validating", "jobs.letterbox_detect.progress.validating"),
    ),
    tool_adapter="ffprobe_ffmpeg_cropdetect",
    persistence_cadence_seconds=2,
    meaningful_delta_percent=None,
    max_snapshot_staleness_seconds=15,
    eta_capability=False,
)

_DOVI_ANALYZE_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.INDETERMINATE,
    overall_unit="probe phases",
    denominator_source="none",
    current_unit="phase",
    aggregation_strategy="none",
    stages=(
        ("probing", "jobs.dovi_analyze.progress.probing"),
        ("analyzing", "jobs.dovi_analyze.progress.analyzing"),
        ("validating", "jobs.dovi_analyze.progress.validating"),
    ),
    tool_adapter="ffprobe_dovi_tool",
    persistence_cadence_seconds=2,
    meaningful_delta_percent=None,
    max_snapshot_staleness_seconds=15,
    eta_capability=False,
)

_POSTER_PIPELINE_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.HYBRID,
    overall_unit="stages",
    denominator_source="declared_candidate_sources",
    current_unit="candidates",
    aggregation_strategy="current_scope",
    stages=(
        ("resolving", "jobs.poster_pipeline.progress.resolving"),
        ("enumerating", "jobs.poster_pipeline.progress.enumerating"),
        ("downloading", "jobs.poster_pipeline.progress.downloading"),
        ("validating", "jobs.poster_pipeline.progress.validating"),
        ("deduplicating", "jobs.poster_pipeline.progress.deduplicating"),
        ("extracting", "jobs.poster_pipeline.progress.extracting"),
        ("scoring", "jobs.poster_pipeline.progress.scoring"),
        ("rendering", "jobs.poster_pipeline.progress.rendering"),
        ("finalizing", "jobs.poster_pipeline.progress.finalizing"),
    ),
    tool_adapter="poster_analysis_adapter",
    persistence_cadence_seconds=2,
    meaningful_delta_percent=1,
    max_snapshot_staleness_seconds=10,
    eta_capability=False,
)

_ML_PUBLICATION_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.HYBRID,
    overall_unit="stages",
    denominator_source="registered_stages",
    current_unit="stage",
    aggregation_strategy="current_scope",
    stages=(
        ("loading", "jobs.ml.progress.loading"),
        ("collecting", "jobs.ml.progress.collecting"),
        ("features", "jobs.ml.progress.features"),
        ("training", "jobs.ml.progress.training"),
        ("evaluating", "jobs.ml.progress.evaluating"),
        ("validating", "jobs.ml.progress.validating"),
        ("registering", "jobs.ml.progress.registering"),
        ("publishing", "jobs.ml.progress.publishing"),
    ),
    tool_adapter="immutable_ml_publication",
    persistence_cadence_seconds=2,
    meaningful_delta_percent=1,
    max_snapshot_staleness_seconds=10,
    eta_capability=False,
)

_POSTER_RESCAN_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.DETERMINATE,
    overall_unit="subjects",
    denominator_source="resolved_subject_snapshot",
    current_unit="subjects",
    aggregation_strategy="current_scope",
    stages=(("reconciling", "jobs.poster_rescan.progress.reconciling"),),
    tool_adapter="bounded_filesystem_observation",
    persistence_cadence_seconds=2,
    meaningful_delta_percent=1,
    max_snapshot_staleness_seconds=10,
    eta_capability=True,
)

# Per-type progress policy overrides. Types absent here use the generic `_progress(spec)`.
_PROGRESS_POLICIES: dict[str, ProgressPolicy] = {
    "library_sync": _LIBRARY_SYNC_PROGRESS,
    "subtitle_scan": _SUBTITLE_SCAN_PROGRESS,
    "subtitle_policy_audit": _SUBTITLE_POLICY_AUDIT_PROGRESS,
    "letterbox_detect": _LETTERBOX_DETECT_PROGRESS,
    "letterbox_detect_episode": _LETTERBOX_DETECT_PROGRESS,
    "letterbox_detect_tv_scope": _LETTERBOX_DETECT_PROGRESS,
    "dovi_analyze": _DOVI_ANALYZE_PROGRESS,
    "poster_pipeline": _POSTER_PIPELINE_PROGRESS,
    "poster_rescan": _POSTER_RESCAN_PROGRESS,
    "taste_rebuild": _ML_PUBLICATION_PROGRESS,
    "taste_map": _ML_PUBLICATION_PROGRESS,
    "learned_head_train": _ML_PUBLICATION_PROGRESS,
}

_NATIVE_FFMPEG = frozenset({"dovi_convert", "letterbox_reencode"})
_NATIVE_MKVMERGE = frozenset(
    {
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "subtitle_embed",
        "subtitle_metadata",
        "audio_reorder",
        "subtitle_extract",
        "subtitle_policy",
        "subtitle_restore",
    }
)


def _triggers(job_type: str) -> frozenset[TriggerKind]:
    if job_type in WEBHOOK_RESERVED_TYPES:
        return frozenset({TriggerKind.WEBHOOK})
    values = {TriggerKind.PARENT if job_type in PARENT_ONLY_TYPES else TriggerKind.MANUAL}
    if job_type in PARENT_ONLY_TYPES:
        values.add(TriggerKind.BATCH)
    if job_type in _BATCH_CHILD_TYPES:
        values.add(TriggerKind.BATCH)
    if job_type in HEALING_TYPES:
        values.add(TriggerKind.HEALING)
    if job_type in SCHEDULE_PRODUCED_TYPES:
        values.add(TriggerKind.SCHEDULE)
    if job_type == "system_noop":
        values = {TriggerKind.SYSTEM}
    return frozenset(values)


def _subject_builder(job_type: str, kinds: frozenset[str]) -> Callable[[Any], SubjectSnapshot]:
    def build(value: Any) -> SubjectSnapshot:
        snapshot = SUBJECT_SNAPSHOT_ADAPTER.validate_python(value)
        if snapshot.kind not in kinds:
            raise ValueError(
                f"{job_type} requires subject kind in {sorted(kinds)}; got {snapshot.kind}"
            )
        return snapshot

    build.__name__ = f"build_{job_type}_subject"
    return build


def _progress(spec: _DefinitionSpec) -> ProgressPolicy:
    prefix = f"jobs.{spec.job_type}.progress"
    stages = (
        ("prepare", f"{prefix}.prepare"),
        ("execute", f"{prefix}.execute"),
        ("finalize", f"{prefix}.finalize"),
    )
    if spec.progress == ProgressStrategy.NONE:
        return ProgressPolicy(
            strategy=spec.progress,
            overall_unit=None,
            denominator_source="none",
            current_unit=None,
            aggregation_strategy="none",
            stages=(("execute", f"{prefix}.execute"),),
            tool_adapter=None,
            persistence_cadence_seconds=5,
            meaningful_delta_percent=None,
            max_snapshot_staleness_seconds=15,
            eta_capability=False,
        )
    tool_adapter = None
    if spec.job_type in _NATIVE_FFMPEG:
        tool_adapter = "ffmpeg_progress"
    elif spec.job_type in _NATIVE_MKVMERGE:
        tool_adapter = "mkvmerge_gui"
    determinate = spec.progress == ProgressStrategy.DETERMINATE
    return ProgressPolicy(
        strategy=spec.progress,
        overall_unit="children" if determinate else "subjects",
        denominator_source="sealed_child_count" if determinate else "known_subject_count",
        current_unit=None if determinate else "work",
        aggregation_strategy="terminal_child_count" if determinate else "nested_subject",
        stages=stages,
        tool_adapter=tool_adapter,
        persistence_cadence_seconds=2,
        meaningful_delta_percent=1,
        max_snapshot_staleness_seconds=10,
        eta_capability=determinate or tool_adapter is not None,
        allow_concurrent_subjects=spec.job_type in PARENT_ONLY_TYPES,
    )


def _definition(spec: _DefinitionSpec) -> JobDefinition:
    enabled = spec.job_type in ENABLED_JOB_TYPES
    is_noop = spec.job_type == "system_noop"
    parent_only = spec.job_type in PARENT_ONLY_TYPES
    request_model = _REQUEST_MODELS.get(spec.job_type) or (
        SystemNoopRequestV1 if is_noop else BuiltInIntentV1
    )
    retry = RetryPolicy(
        max_attempts=1 if spec.safety == EffectSafety.UNSAFE_MUTATION else 3,
        transient_delays_seconds=() if spec.safety == EffectSafety.UNSAFE_MUTATION else (5, 30),
    )
    return JobDefinition(
        job_type=spec.job_type,
        label_key=f"jobs.{spec.job_type}.label",
        feature_area=spec.feature,
        presentation_family=spec.feature.value,
        presenter_key=f"jobs.{spec.job_type}",
        enabled=enabled,
        migration_state=(
            MigrationState.ENABLED
            if enabled
            else MigrationState.PARENT_ONLY
            if parent_only
            else MigrationState.DEFINED_DISABLED
        ),
        disabled_reason=None if enabled else "execution migration is deferred beyond JMC2B",
        request=current_adapter(DocumentKind.REQUEST, request_model),
        result=current_adapter(DocumentKind.RESULT, _RESULT_MODELS.get(spec.job_type, BuiltInResultV1)),
        error=current_adapter(DocumentKind.ERROR, SafeJobErrorV1),
        execution_class=spec.execution,
        entrypoint=spec.execution.value,
        timeout=TimeoutPolicy(seconds=30 if is_noop else 24 * 60 * 60),
        effect_safety=spec.safety,
        configuration_keys=(
            frozenset(
                {
                    "AUDIO_SUBS_DEEP_SCAN_ENABLED",
                    "AUDIO_SUBS_DEEP_SCAN_HOUR",
                    "AUDIO_SUBS_DEEP_SCAN_BATCH",
                }
            )
            if spec.job_type == "audio_subs_deep_scan"
            else frozenset()
        ),
        subject_builder=_subject_builder(spec.job_type, spec.subject_kinds),
        progress_policy=_PROGRESS_POLICIES.get(spec.job_type) or _progress(spec),
        retry_policy=retry,
        action_policy=ActionPolicy(
            pause=False,
            logs=spec.execution != ExecutionClass.CONTROL,
            artifacts=spec.execution in {ExecutionClass.MEDIA_WRITE, ExecutionClass.GPU},
        ),
        parent_policy=ParentAggregationPolicy(fixed_children=True) if parent_only else None,
        trigger_kinds=_triggers(spec.job_type),
        child_job_types=spec.child_job_types,
        subject_kinds=spec.subject_kinds,
    )


def build_job_definition_registry() -> JobDefinitionRegistry:
    registry = JobDefinitionRegistry(_definition(spec) for spec in _SPECS)
    registry.validate_complete(BUILTIN_JOB_TYPES)
    return registry


JOB_DEFINITION_REGISTRY = build_job_definition_registry()
