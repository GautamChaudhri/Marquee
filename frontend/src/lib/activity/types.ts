/**
 * Canonical wire types for the shared Activity client, aliased straight from the
 * generated OpenAPI `components`/`paths`. The generated schema is the single wire
 * authority (JMC6A A02); nothing here re-declares a shape. Consumers (store,
 * cards, feature pages) import these instead of the handwritten legacy DTOs in
 * `lib/api/jobs.ts`.
 */
import type { components, paths } from '../api/generated/openapi';

type Schemas = components['schemas'];

export type JobRow = Schemas['JobRow'];
export type JobListResponse = Schemas['JobListResponse'];
export type JobSnapshotResponse = Schemas['JobSnapshotResponse'];
export type JobPresentation = Schemas['JobPresentation'];
export type PresentationSection = JobPresentation['sections'][number];
export type PresentationValue = Schemas['Fact']['value'];
export type BatchSummaryResponse = Schemas['BatchSummaryResponse'];
export type WorkItemPage = Schemas['WorkItemPage'];
export type WorkItemRow = Schemas['WorkItemRow'];
export type WorkItemSummary = Schemas['WorkItemSummary'];
export type WorkItemStatusCounts = Schemas['WorkItemStatusCounts'];
export type ContainedWorkPage = Schemas['ContainedWorkPage'];
export type ContainedWorkItem = Schemas['ContainedWorkItem'];
export type ContainedWorkSummary = Schemas['ContainedWorkSummary'];
export type ActivityCatalogResponse = Schemas['ActivityCatalogResponse'];
export type ActivityAttentionResponse = Schemas['ActivityAttentionResponse'];
export type OperationsSnapshot = Schemas['OperationsSnapshot'];
export type OperationsHistoryResponse = Schemas['OperationsHistoryResponse'];

export type CompactProgress = Schemas['CompactProgress'];
export type ProgressMeasurement = Schemas['ProgressMeasurement'];
export type ProgressFreshness = Schemas['ProgressFreshness'];
export type ProgressWait = Schemas['ProgressWait'];
export type MeasurementMode = Schemas['MeasurementMode'];
export type PresentationSubject = Schemas['PresentationSubject'];
export type PresentationStatus = Schemas['PresentationStatus'];
export type PresentationAttention = Schemas['PresentationAttention'];
export type PresentationImpact = Schemas['PresentationImpact'];
export type EvidenceAvailability = Schemas['EvidenceAvailability'];
export type DiagnosticLinks = Schemas['DiagnosticLinks'];
export type MetricCard = Schemas['MetricCard'];
export type MetricCardsSection = Schemas['MetricCardsSection'];
export type ChildrenSection = Schemas['ChildrenSection'];

export type JobEventFrame = Schemas['JobEventFrame'];
export type JobEventDelta = Schemas['JobEventDelta'];
export type EventReconciliation = Schemas['EventReconciliation'];
export type EventItem = Schemas['EventItem'];
export type EventListResponse = Schemas['EventListResponse'];

export type AttemptListResponse = Schemas['AttemptListResponse'];
export type AttemptItem = Schemas['AttemptItem'];
export type AttemptLogPage = Schemas['AttemptLogPage'];
export type AttemptLogLine = Schemas['AttemptLogLine'];
export type ChildListResponse = Schemas['ChildListResponse'];
export type ArtifactListResponse = Schemas['ArtifactListResponse'];
export type ArtifactItemResponse = Schemas['ArtifactItemResponse'];

export type CommandResponse = Schemas['CommandResponse'];
export type CommandRequest = Schemas['CommandRequest'];
export type PriorityUpdateRequest = Schemas['PriorityUpdateRequest'];
export type BulkActionRequest = Schemas['BulkActionRequest'];
export type BulkActionResponse = Schemas['BulkActionResponse'];
export type BulkActionItem = Schemas['BulkActionItem'];
export type JobSubmissionResponse = Schemas['JobSubmissionResponse'];

export type JobAction = Schemas['JobAction'];
export type FeatureArea = Schemas['FeatureArea'];
export type AttentionLevel = Schemas['AttentionLevel'];
export type JobApiErrorDetail = Schemas['JobApiErrorDetail'];

/** Bounded query for `GET /api/jobs`, straight from the generated path operation. */
export type ListJobsQuery = NonNullable<paths['/api/jobs']['get']['parameters']['query']>;
export type WorkItemsQuery = NonNullable<
	paths['/api/jobs/{job_id}/work-items']['get']['parameters']['query']
>;
export type ContainedWorkQuery = NonNullable<
	paths['/api/jobs/{job_id}/contained-work']['get']['parameters']['query']
>;

/** The `kind` path segment of `GET /api/jobs/{job_id}/raw/{kind}`. */
export type RawDocumentKind =
	paths['/api/jobs/{job_id}/raw/{kind}']['get']['parameters']['path']['kind'];
