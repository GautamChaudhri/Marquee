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
    SafeJobErrorV1,
    SystemNoopRequestV1,
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
    _spec("subtitle_scan_all", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_READ, _R, "maintenance_scope"),
    _spec("audio_subs_deep_scan", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_READ, _R, "maintenance_scope"),
    _spec("radarr_upgrade", FeatureArea.LIBRARY_INTEGRATIONS, ExecutionClass.NETWORK, _R, "movie"),
    _spec("poster_pipeline", FeatureArea.AI_POSTERS, ExecutionClass.GPU, _U, "movie", "poster_candidate_set"),
    _spec("poster_pipeline_batch", FeatureArea.AI_POSTERS, ExecutionClass.GPU, _U, "aggregate_batch"),
    _spec("poster_pipeline_tv_batch", FeatureArea.AI_POSTERS, ExecutionClass.GPU, _U, "aggregate_batch"),
    _spec("learned_head_train", FeatureArea.ML_TASTE, ExecutionClass.GPU, _R, "model_profile_training"),
    _spec("pipeline_cache_clear", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("poster_deploy_reset", FeatureArea.AI_POSTERS, ExecutionClass.NETWORK, _U, "maintenance_scope"),
    _spec("poster_rescan", FeatureArea.AI_POSTERS, ExecutionClass.CPU, _R, "poster_candidate_set"),
    _spec("poster_backup_all", FeatureArea.AI_POSTERS, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("poster_maintenance", FeatureArea.AI_POSTERS, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("job_retention_purge", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("system_metrics_purge", FeatureArea.MAINTENANCE, ExecutionClass.MAINTENANCE, _U, "maintenance_scope"),
    _spec("dovi_analyze", FeatureArea.HDR, ExecutionClass.MEDIA_READ, _R, "media_file", "movie", "episode"),
    _spec("dovi_convert", FeatureArea.HDR, ExecutionClass.MEDIA_WRITE, _U, "media_file", "movie", "episode"),
    _spec("subtitle_scan", FeatureArea.AUDIO_SUBTITLES, ExecutionClass.MEDIA_READ, _R, "media_file"),
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
    _spec("letterbox_detect_tv_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_detect_episode",)),
    _spec("letterbox_detect_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_detect",)),
    _spec("letterbox_apply_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_apply",)),
    _spec("letterbox_reencode_tv_batch", FeatureArea.LETTERBOX, ExecutionClass.CONTROL, _R, "aggregate_batch", progress=ProgressStrategy.DETERMINATE, children=("letterbox_reencode",)),
)

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
    enabled = spec.job_type == "system_noop"
    parent_only = spec.job_type in PARENT_ONLY_TYPES
    request_model = SystemNoopRequestV1 if enabled else BuiltInIntentV1
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
        result=current_adapter(DocumentKind.RESULT, BuiltInResultV1),
        error=current_adapter(DocumentKind.ERROR, SafeJobErrorV1),
        execution_class=spec.execution,
        entrypoint=spec.execution.value,
        timeout=TimeoutPolicy(seconds=30 if enabled else 24 * 60 * 60),
        effect_safety=spec.safety,
        configuration_keys=frozenset(),
        subject_builder=_subject_builder(spec.job_type, spec.subject_kinds),
        progress_policy=_progress(spec),
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

