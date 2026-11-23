"""Complete immutable manifest for every inventoried built-in job type."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from marquee.core.configuration import CONFIGURATION_CATALOG
from marquee.core.jobs.contracts import (
    EffectSafety,
    ExecutionClass,
    FeatureArea,
    MigrationState,
    ProgressStrategy,
    TriggerKind,
)
from marquee.core.jobs.definitions import (
    ActiveOverlapMode,
    ActiveOverlapPolicy,
    ActivityPolicy,
    ActivityVisibility,
    ContainedWorkSource,
    JobDefinition,
    JobDefinitionRegistry,
    TimeoutPolicy,
)
from marquee.core.jobs.documents import (
    BuiltInIntentV1,
    BuiltInResultV1,
    DocumentKind,
    LibrarySyncRequestV1,
    MlPublicationResultV1,
    PosterBatchRequestV1,
    PosterPipelineGroupRequestV1,
    PosterPipelineGroupResultV1,
    PosterPipelineRequestV1,
    PosterPipelineResultV1,
    PosterRescanRequestV1,
    PosterRescanResultV1,
    RankingResidualTrainRequestV1,
    SafeJobErrorV1,
    StrictDocument,
    SystemNoopRequestV1,
    TasteEnrichRequestV1,
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
from marquee.core.jobs.mutation_documents import (
    BackupCreateRequestV1,
    MaintenanceErrorV1,
    MaintenanceResultV1,
    MetricsPurgeRequestV1,
    MutationErrorV1,
    PipelineCacheClearRequestV1,
    PosterBackupRequestV1,
    PosterDeployRequestV1,
    PosterMaintenanceRequestV1,
    PosterMutationResultV1,
    PosterParentRequestV1,
    PosterResetRequestV1,
    PosterRestoreRequestV1,
    RetentionPurgeRequestV1,
)
from marquee.core.jobs.policies import (
    ActionPolicy,
    ParentAggregationPolicy,
    RetryMode,
    RetryPolicy,
    default_failure_classifier,
)
from marquee.core.jobs.progress import ProgressPolicy
from marquee.core.jobs.safety_gates import SafetyPolicy
from marquee.core.jobs.subjects import SUBJECT_SNAPSHOT_ADAPTER, SubjectSnapshot
from marquee.core.jobs.terminal_decision import JobOutcome, TerminalDecisionPolicy


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

_FEATURE_LABELS = {
    FeatureArea.AI_POSTERS: "Posters",
    FeatureArea.LIBRARY_INTEGRATIONS: "Library integrations",
    FeatureArea.ML_TASTE: "Taste and ranking",
    FeatureArea.MAINTENANCE: "Maintenance",
    FeatureArea.SYSTEM: "System",
}


def _activity_policy(spec: _DefinitionSpec) -> ActivityPolicy:
    common = {"feature_label": _FEATURE_LABELS[spec.feature]}
    if spec.job_type in {"poster_pipeline_batch", "poster_pipeline_tv_batch"}:
        return ActivityPolicy(
            visibility=ActivityVisibility.PROMOTE_CHILDREN,
            contained_work=ContainedWorkSource.CHILD_JOBS,
            promoted_child_types=frozenset({"poster_pipeline_group"}),
            hidden_child_types=frozenset({"poster_pipeline"}),
            item_label_singular="subject",
            item_label_plural="subjects",
            # A batch owns every chunk of one run, so its roster is the run.
            disclosure_label="Posters in this run",
            monogram="FP" if spec.job_type == "poster_pipeline_batch" else "TVP",
            **common,
        )
    if spec.job_type == "poster_pipeline_group":
        return ActivityPolicy(
            contained_work=ContainedWorkSource.WORK_ITEMS,
            hidden_child_types=frozenset({"poster_pipeline"}),
            item_label_singular="subject",
            item_label_plural="subjects",
            # A unified run has one group, and calling it a group there is
            # misleading; ``poster_group_disclosure_label`` overrides this per
            # job once the batch mode is known.
            disclosure_label="Posters in this group",
            monogram="P",
            stage_catalog_key="poster_pipeline",
            **common,
        )
    parent_copy = {
        "poster_heal": ("Posters Being Healed", "PH"),
        "poster_deploy_reset": ("Posters Being Reset", "PR"),
        "poster_backup_all": ("Posters Being Backed Up", "PB"),
    }
    if spec.job_type in parent_copy:
        disclosure, monogram = parent_copy[spec.job_type]
        return ActivityPolicy(
            visibility=ActivityVisibility.CONSOLIDATE_PARENT,
            contained_work=ContainedWorkSource.CHILD_JOBS,
            hidden_child_types=spec.child_job_types,
            item_label_singular="subject",
            item_label_plural="subjects",
            disclosure_label=disclosure,
            monogram=monogram,
            **common,
        )
    return ActivityPolicy(**common)


_SPECS = (
    _spec(
        "system_noop",
        FeatureArea.SYSTEM,
        ExecutionClass.CONTROL,
        _R,
        "system_work",
        progress=ProgressStrategy.NONE,
    ),
    _spec(
        "poster_heal",
        FeatureArea.AI_POSTERS,
        ExecutionClass.CONTROL,
        _R,
        "aggregate_batch",
        progress=ProgressStrategy.DETERMINATE,
        children=("poster_restore",),
    ),
    _spec(
        "backup_create",
        FeatureArea.MAINTENANCE,
        ExecutionClass.MAINTENANCE,
        _U,
        "maintenance_scope",
    ),
    _spec("taste_rebuild", FeatureArea.ML_TASTE, ExecutionClass.GPU, _R, "model_profile_training"),
    _spec("taste_map", FeatureArea.ML_TASTE, ExecutionClass.CPU, _R, "model_profile_training"),
    _spec("taste_enrich", FeatureArea.ML_TASTE, ExecutionClass.CPU, _R, "model_profile_training"),
    _spec(
        "library_sync",
        FeatureArea.LIBRARY_INTEGRATIONS,
        ExecutionClass.NETWORK,
        _R,
        "maintenance_scope",
    ),
    _spec("radarr_upgrade", FeatureArea.LIBRARY_INTEGRATIONS, ExecutionClass.NETWORK, _R, "movie"),
    _spec(
        "poster_pipeline",
        FeatureArea.AI_POSTERS,
        ExecutionClass.GPU,
        _R,
        "movie",
        "series",
        "season",
        "episode",
    ),
    _spec(
        "poster_pipeline_group",
        FeatureArea.AI_POSTERS,
        ExecutionClass.GPU,
        _R,
        "poster_subject_group",
    ),
    _spec(
        "poster_deploy",
        FeatureArea.AI_POSTERS,
        ExecutionClass.MEDIA_WRITE,
        _U,
        "movie",
        "series",
        "season",
    ),
    _spec(
        "poster_restore",
        FeatureArea.AI_POSTERS,
        ExecutionClass.MEDIA_WRITE,
        _U,
        "movie",
        "series",
        "season",
    ),
    _spec(
        "poster_reset",
        FeatureArea.AI_POSTERS,
        ExecutionClass.MEDIA_WRITE,
        _U,
        "movie",
        "series",
        "season",
    ),
    _spec(
        "poster_backup_subject",
        FeatureArea.AI_POSTERS,
        ExecutionClass.MEDIA_WRITE,
        _U,
        "movie",
        "series",
        "season",
    ),
    _spec(
        "poster_pipeline_batch",
        FeatureArea.AI_POSTERS,
        ExecutionClass.CONTROL,
        _R,
        "aggregate_batch",
        progress=ProgressStrategy.DETERMINATE,
        children=("poster_pipeline", "poster_pipeline_group"),
    ),
    _spec(
        "poster_pipeline_tv_batch",
        FeatureArea.AI_POSTERS,
        ExecutionClass.CONTROL,
        _R,
        "aggregate_batch",
        progress=ProgressStrategy.DETERMINATE,
        children=("poster_pipeline", "poster_pipeline_group"),
    ),
    _spec(
        "ranking_residual_train",
        FeatureArea.ML_TASTE,
        ExecutionClass.CPU,
        _R,
        "model_profile_training",
    ),
    _spec(
        "pipeline_cache_clear",
        FeatureArea.MAINTENANCE,
        ExecutionClass.MAINTENANCE,
        _U,
        "maintenance_scope",
        progress=ProgressStrategy.DETERMINATE,
    ),
    _spec(
        "poster_deploy_reset",
        FeatureArea.AI_POSTERS,
        ExecutionClass.CONTROL,
        _R,
        "aggregate_batch",
        progress=ProgressStrategy.DETERMINATE,
        children=("poster_reset",),
    ),
    _spec(
        "poster_rescan",
        FeatureArea.AI_POSTERS,
        ExecutionClass.MEDIA_READ,
        _R,
        "poster_candidate_set",
    ),
    _spec(
        "poster_backup_all",
        FeatureArea.AI_POSTERS,
        ExecutionClass.CONTROL,
        _R,
        "aggregate_batch",
        progress=ProgressStrategy.DETERMINATE,
        children=("poster_backup_subject",),
    ),
    _spec(
        "poster_maintenance",
        FeatureArea.AI_POSTERS,
        ExecutionClass.MAINTENANCE,
        _U,
        "maintenance_scope",
        progress=ProgressStrategy.DETERMINATE,
    ),
    _spec(
        "job_retention_purge",
        FeatureArea.MAINTENANCE,
        ExecutionClass.MAINTENANCE,
        _U,
        "maintenance_scope",
        progress=ProgressStrategy.DETERMINATE,
    ),
    _spec(
        "system_metrics_purge",
        FeatureArea.MAINTENANCE,
        ExecutionClass.MAINTENANCE,
        _U,
        "maintenance_scope",
        progress=ProgressStrategy.DETERMINATE,
    ),
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
        "poster_pipeline",
        "poster_pipeline_group",
        "poster_deploy",
        "poster_backup_subject",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "poster_rescan",
        "poster_reset",
        "poster_restore",
        "taste_rebuild",
        "taste_map",
        "taste_enrich",
        "ranking_residual_train",
    }
)

# Per-type request document models. Types absent here fall back to the generic BuiltInIntentV1
# (or the tiny SystemNoopRequestV1 for system_noop). Results stay generic BuiltInResultV1.
_REQUEST_MODELS: dict[str, type[StrictDocument]] = {
    "library_sync": LibrarySyncRequestV1,
    "poster_pipeline": PosterPipelineRequestV1,
    "poster_pipeline_group": PosterPipelineGroupRequestV1,
    "poster_pipeline_batch": PosterBatchRequestV1,
    "poster_pipeline_tv_batch": PosterBatchRequestV1,
    "taste_rebuild": TasteRebuildRequestV1,
    "taste_map": TasteMapRequestV1,
    "taste_enrich": TasteEnrichRequestV1,
    "ranking_residual_train": RankingResidualTrainRequestV1,
    "poster_rescan": PosterRescanRequestV1,
    "poster_deploy": PosterDeployRequestV1,
    "poster_restore": PosterRestoreRequestV1,
    "poster_reset": PosterResetRequestV1,
    "poster_backup_subject": PosterBackupRequestV1,
    "poster_deploy_reset": PosterParentRequestV1,
    "poster_backup_all": PosterParentRequestV1,
    "poster_heal": PosterParentRequestV1,
    "backup_create": BackupCreateRequestV1,
    "poster_maintenance": PosterMaintenanceRequestV1,
    "pipeline_cache_clear": PipelineCacheClearRequestV1,
    "job_retention_purge": RetentionPurgeRequestV1,
    "system_metrics_purge": MetricsPurgeRequestV1,
}

_RESULT_MODELS: dict[str, type[StrictDocument]] = {
    "poster_pipeline": PosterPipelineResultV1,
    "poster_pipeline_group": PosterPipelineGroupResultV1,
    "poster_rescan": PosterRescanResultV1,
    "taste_rebuild": MlPublicationResultV1,
    "taste_map": MlPublicationResultV1,
    "taste_enrich": MlPublicationResultV1,
    "ranking_residual_train": MlPublicationResultV1,
    "poster_deploy": PosterMutationResultV1,
    "poster_restore": PosterMutationResultV1,
    "poster_reset": PosterMutationResultV1,
    "poster_backup_subject": PosterMutationResultV1,
    "backup_create": MaintenanceResultV1,
    "poster_maintenance": MaintenanceResultV1,
    "pipeline_cache_clear": MaintenanceResultV1,
    "job_retention_purge": MaintenanceResultV1,
    "system_metrics_purge": MaintenanceResultV1,
}

_ERROR_MODELS: dict[str, type[StrictDocument]] = {
    "poster_deploy": MutationErrorV1,
    "poster_restore": MutationErrorV1,
    "poster_reset": MutationErrorV1,
    "poster_backup_subject": MutationErrorV1,
    "backup_create": MaintenanceErrorV1,
    "poster_maintenance": MaintenanceErrorV1,
    "pipeline_cache_clear": MaintenanceErrorV1,
    "job_retention_purge": MaintenanceErrorV1,
    "system_metrics_purge": MaintenanceErrorV1,
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

_POSTER_PIPELINE_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.HYBRID,
    overall_unit="stages",
    denominator_source="declared_candidate_sources",
    current_unit="candidates",
    aggregation_strategy="current_scope",
    # Declared in the order the pipeline actually runs them, so a stage's
    # position never goes backwards as the run advances. ``_STAGE_MAP`` in
    # marquee.core.jobs.poster_pipeline folds the runner's own stage names onto
    # these, and its ordinals must stay non-decreasing: the overall bar reports
    # the furthest position reached while the roster reports the position each
    # subject is at, and only a monotonic vocabulary makes those the same number.
    stages=(
        ("resolving", "jobs.poster_pipeline.progress.resolving"),
        ("enumerating", "jobs.poster_pipeline.progress.enumerating"),
        ("downloading", "jobs.poster_pipeline.progress.downloading"),
        ("deduplicating", "jobs.poster_pipeline.progress.deduplicating"),
        ("validating", "jobs.poster_pipeline.progress.validating"),
        ("extracting", "jobs.poster_pipeline.progress.extracting"),
        ("filtering", "jobs.poster_pipeline.progress.filtering"),
        ("analyzing", "jobs.poster_pipeline.progress.analyzing"),
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
    current_unit="items",
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

_POSTER_MUTATION_PROGRESS = ProgressPolicy(
    strategy=ProgressStrategy.INDETERMINATE,
    overall_unit="targets",
    denominator_source="single_target",
    current_unit="steps",
    aggregation_strategy="current_target",
    stages=tuple(
        (stage, f"jobs.poster_mutation.progress.{stage}")
        for stage in (
            "resolving",
            "snapshotting",
            "backing_up",
            "staging",
            "validating",
            "publishing",
            "finalizing",
        )
    ),
    tool_adapter=None,
    persistence_cadence_seconds=2,
    meaningful_delta_percent=1,
    max_snapshot_staleness_seconds=10,
    eta_capability=False,
)

# Per-type progress policy overrides. Types absent here use the generic `_progress(spec)`.
_PROGRESS_POLICIES: dict[str, ProgressPolicy] = {
    "library_sync": _LIBRARY_SYNC_PROGRESS,
    "poster_pipeline": _POSTER_PIPELINE_PROGRESS,
    "poster_pipeline_group": _POSTER_PIPELINE_PROGRESS,
    "poster_rescan": _POSTER_RESCAN_PROGRESS,
    "poster_deploy": _POSTER_MUTATION_PROGRESS,
    "poster_restore": _POSTER_MUTATION_PROGRESS,
    "poster_reset": _POSTER_MUTATION_PROGRESS,
    "poster_backup_subject": _POSTER_MUTATION_PROGRESS,
    "taste_rebuild": _ML_PUBLICATION_PROGRESS,
    "taste_map": _ML_PUBLICATION_PROGRESS,
    "taste_enrich": _ML_PUBLICATION_PROGRESS,
    "ranking_residual_train": _ML_PUBLICATION_PROGRESS,
}

_PIPELINE_CONFIGURATION_KEYS = frozenset(
    key
    for key, entry in CONFIGURATION_CATALOG.items()
    if entry.owner == "pipeline"
    and entry.scope == "execution"
    and entry.database_owned
    and entry.sensitivity == "public"
)
_CONFIGURATION_KEYS_BY_TYPE = dict.fromkeys(
    {
        "poster_pipeline",
        "poster_pipeline_group",
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "taste_rebuild",
        "taste_map",
        "taste_enrich",
        "ranking_residual_train",
    },
    _PIPELINE_CONFIGURATION_KEYS,
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


def _overlap_policy(spec: _DefinitionSpec) -> ActiveOverlapPolicy:
    if spec.job_type == "system_noop":
        mode = ActiveOverlapMode.ALLOW
    elif spec.safety == EffectSafety.UNSAFE_MUTATION:
        mode = ActiveOverlapMode.REJECT_CONFLICT
    else:
        mode = ActiveOverlapMode.COALESCE_EQUIVALENT
    return ActiveOverlapPolicy(mode=mode)


def _retry_mode(spec: _DefinitionSpec) -> RetryMode:
    """Every registry definition declares one user-retry contract at build time."""
    if spec.job_type == "system_noop":
        return RetryMode.GENERIC
    if (
        spec.job_type
        in {"poster_pipeline", "poster_pipeline_group", "poster_deploy", "taste_rebuild"}
        or spec.job_type in PARENT_ONLY_TYPES
    ):
        return RetryMode.DOMAIN_COORDINATED
    return RetryMode.UNSUPPORTED


def _definition(spec: _DefinitionSpec) -> JobDefinition:
    enabled = spec.job_type in ENABLED_JOB_TYPES
    is_noop = spec.job_type == "system_noop"
    parent_only = spec.job_type in PARENT_ONLY_TYPES
    request_model = _REQUEST_MODELS.get(spec.job_type) or (
        SystemNoopRequestV1 if is_noop else BuiltInIntentV1
    )
    result_model = _RESULT_MODELS.get(spec.job_type, BuiltInResultV1)
    configuration_keys = _CONFIGURATION_KEYS_BY_TYPE.get(spec.job_type, frozenset())
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
        result=current_adapter(DocumentKind.RESULT, result_model),
        error=current_adapter(DocumentKind.ERROR, _ERROR_MODELS.get(spec.job_type, SafeJobErrorV1)),
        execution_class=spec.execution,
        entrypoint=spec.execution.value,
        timeout=TimeoutPolicy(seconds=30 if is_noop else 24 * 60 * 60),
        effect_safety=spec.safety,
        terminal_policy=TerminalDecisionPolicy.for_result_model(
            result_model,
            aliases=(
                {"review_required": JobOutcome.PARTIALLY_SUCCEEDED}
                if result_model in (PosterPipelineResultV1, PosterPipelineGroupResultV1)
                else None
            ),
            # `review_required` borrows `partially_succeeded` for want of a
            # canonical outcome of its own; it must not read as failure.
            review_outcomes=(
                frozenset({"review_required"})
                if result_model in (PosterPipelineResultV1, PosterPipelineGroupResultV1)
                else frozenset()
            ),
        ),
        failure_classifier=default_failure_classifier,
        configuration_audit="snapshot" if configuration_keys else "audited_empty",
        activity_policy=_activity_policy(spec),
        overlap_policy=_overlap_policy(spec),
        safety_policy=(
            SafetyPolicy(media_file=True, media_write=True)
            if spec.execution == ExecutionClass.MEDIA_WRITE
            else SafetyPolicy(exclusive_maintenance=True)
            if spec.execution == ExecutionClass.MAINTENANCE and spec.safety == _U
            else SafetyPolicy()
        ),
        configuration_keys=configuration_keys,
        subject_builder=_subject_builder(spec.job_type, spec.subject_kinds),
        progress_policy=_PROGRESS_POLICIES.get(spec.job_type) or _progress(spec),
        retry_policy=retry,
        retry_mode=_retry_mode(spec),
        action_policy=ActionPolicy(
            pause=False,
            logs=spec.execution != ExecutionClass.CONTROL,
            artifacts=spec.execution
            in {ExecutionClass.MEDIA_WRITE, ExecutionClass.GPU, ExecutionClass.MAINTENANCE},
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
