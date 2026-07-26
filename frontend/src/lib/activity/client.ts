/**
 * The shared Activity job client. Every method is a thin, generated-path wrapper
 * over the kept fetch runtime (`lib/api/client.ts`) — the path/verb are
 * compile-time-checked against the generated OpenAPI `paths`, so a wrapper cannot
 * name a route that does not exist. Critical boundaries (lists, snapshots,
 * commands) run through the runtime validators before any value reaches UI state.
 *
 * This replaces the handwritten job client's two failure modes: it never rebuilds
 * canonical responses into legacy DTOs, and it never fans out across every
 * diagnostic collection at once. Callers pick exactly the resource they need and
 * pass cursors/limits explicitly (JMC6A §4). The legacy `lib/api/jobs.ts`
 * adapters remain only until their feature-page consumers migrate in JMC6B.
 */
import { apiGet, apiSend, type Fetch } from '../api/client';
import type { paths } from '../api/generated/openapi';
import {
	IncompatibleResponseError,
	parseCommandResponse,
	parseListEnvelope,
	parseSnapshot
} from './validators';
import type {
	ActivityAttentionResponse,
	ArtifactListResponse,
	AttemptListResponse,
	AttemptLogPage,
	BatchSummaryResponse,
	BulkActionRequest,
	BulkActionResponse,
	ChildListResponse,
	CommandResponse,
	EventListResponse,
	JobListResponse,
	JobPresentation,
	JobSnapshotResponse,
	ListJobsQuery,
	OperationsHistoryResponse,
	OperationsSnapshot,
	RawDocumentKind
} from './types';

type AttemptsQuery = NonNullable<
	paths['/api/jobs/{job_id}/attempts']['get']['parameters']['query']
>;
type EventsQuery = NonNullable<paths['/api/jobs/{job_id}/events']['get']['parameters']['query']>;
type ChildrenQuery = NonNullable<
	paths['/api/jobs/{job_id}/children']['get']['parameters']['query']
>;
type ArtifactsQuery = NonNullable<
	paths['/api/jobs/{job_id}/artifacts']['get']['parameters']['query']
>;
type AttemptLogsQuery = NonNullable<
	paths['/api/jobs/{job_id}/attempts/{attempt_id}/logs']['get']['parameters']['query']
>;
type OperationsHistoryQuery = NonNullable<
	paths['/api/system/metrics/history']['get']['parameters']['query']
>;

// ---- discovery + reconciliation -------------------------------------------

/** `GET /api/jobs` — the bounded Queue/History list used for active discovery. */
export async function listJobs(fetch: Fetch, query: ListJobsQuery = {}): Promise<JobListResponse> {
	return parseListEnvelope<JobListResponse>(await apiGet(fetch, '/jobs', query), 'job list');
}

/** `GET /api/jobs/attention` — bounded counts for the strip and navigation badge. */
export function getActivityAttention(fetch: Fetch): Promise<ActivityAttentionResponse> {
	return apiGet<ActivityAttentionResponse>(fetch, '/jobs/attention');
}

/** One versioned bounded snapshot for the lazy secondary Operations surface. */
export function getOperations(fetch: Fetch, signal?: AbortSignal): Promise<OperationsSnapshot> {
	return apiGet<OperationsSnapshot>(fetch, '/system/operations', undefined, signal);
}

/** Separately bounded/downsampled host history, loaded only when expanded. */
export function getOperationsHistory(
	fetch: Fetch,
	query: OperationsHistoryQuery = {},
	signal?: AbortSignal
): Promise<OperationsHistoryResponse> {
	return apiGet<OperationsHistoryResponse>(fetch, '/system/metrics/history', query, signal);
}

/** `GET /api/jobs/{id}/snapshot` — the only endpoint used for active reconciliation. */
export async function getSnapshot(fetch: Fetch, jobId: string): Promise<JobSnapshotResponse> {
	return parseSnapshot(await apiGet(fetch, `/jobs/${jobId}/snapshot`));
}

// ---- bounded diagnostic resources (formerly the getJobDetail fan-out) ------

/** `GET /api/jobs/{id}/presentation` — full versioned presentation for detail pages. */
export function getPresentation(fetch: Fetch, jobId: string): Promise<JobPresentation> {
	return apiGet<JobPresentation>(fetch, `/jobs/${jobId}/presentation`);
}

/** `GET /api/jobs/{id}/batch` — bounded batch-parent rollup. */
export function getBatchSummary(fetch: Fetch, jobId: string): Promise<BatchSummaryResponse> {
	return apiGet<BatchSummaryResponse>(fetch, `/jobs/${jobId}/batch`);
}

/** `GET /api/jobs/{id}/attempts` — the execution-audit attempts, paginated. */
export async function listAttempts(
	fetch: Fetch,
	jobId: string,
	query: AttemptsQuery = {},
	signal?: AbortSignal
): Promise<AttemptListResponse> {
	return parseListEnvelope<AttemptListResponse>(
		await apiGet(fetch, `/jobs/${jobId}/attempts`, query, signal),
		'attempt list'
	);
}

/** `GET /api/jobs/{id}/events` — bounded semantic event replay (cursor `after`). */
export async function listEvents(
	fetch: Fetch,
	jobId: string,
	query: EventsQuery = {},
	signal?: AbortSignal
): Promise<EventListResponse> {
	return parseListEnvelope<EventListResponse>(
		await apiGet(fetch, `/jobs/${jobId}/events`, query, signal),
		'event list'
	);
}

/** `GET /api/jobs/{id}/children` — bounded batch-child list. */
export async function listChildren(
	fetch: Fetch,
	jobId: string,
	query: ChildrenQuery = {}
): Promise<ChildListResponse> {
	return parseListEnvelope<ChildListResponse>(
		await apiGet(fetch, `/jobs/${jobId}/children`, query),
		'child list'
	);
}

/** `GET /api/jobs/{id}/artifacts` — bounded artifact metadata list. */
export async function listArtifacts(
	fetch: Fetch,
	jobId: string,
	query: ArtifactsQuery = {},
	signal?: AbortSignal
): Promise<ArtifactListResponse> {
	return parseListEnvelope<ArtifactListResponse>(
		await apiGet(fetch, `/jobs/${jobId}/artifacts`, query, signal),
		'artifact list'
	);
}

/** `GET /api/jobs/{id}/attempts/{attempt}/logs` — a bounded log-line window. */
export function listAttemptLogs(
	fetch: Fetch,
	jobId: string,
	attemptId: number,
	query: AttemptLogsQuery = {},
	signal?: AbortSignal
): Promise<AttemptLogPage> {
	return apiGet<AttemptLogPage>(fetch, `/jobs/${jobId}/attempts/${attemptId}/logs`, query, signal);
}

/** `GET /api/jobs/{id}/raw/{kind}` — a single virtual raw document (free-form JSON). */
export function getRawDocument(
	fetch: Fetch,
	jobId: string,
	kind: RawDocumentKind,
	signal?: AbortSignal
): Promise<unknown> {
	return apiGet<unknown>(fetch, `/jobs/${jobId}/raw/${kind}`, undefined, signal);
}

// ---- lifecycle commands (validated before their effect is applied) ---------

async function command(
	fetch: Fetch,
	jobId: string,
	action: 'cancel' | 'pause' | 'resume' | 'retry',
	expectedFenceToken: number
): Promise<CommandResponse> {
	return parseCommandResponse(
		await apiSend(fetch, 'POST', `/jobs/${jobId}/${action}`, {
			expected_fence_token: expectedFenceToken
		})
	);
}

export function cancelJob(fetch: Fetch, jobId: string, fence: number): Promise<CommandResponse> {
	return command(fetch, jobId, 'cancel', fence);
}

export function pauseJob(fetch: Fetch, jobId: string, fence: number): Promise<CommandResponse> {
	return command(fetch, jobId, 'pause', fence);
}

export function resumeJob(fetch: Fetch, jobId: string, fence: number): Promise<CommandResponse> {
	return command(fetch, jobId, 'resume', fence);
}

export function retryJob(fetch: Fetch, jobId: string, fence: number): Promise<CommandResponse> {
	return command(fetch, jobId, 'retry', fence);
}

/** `POST /api/jobs/{id}/priority` — reprioritize within the execution class. */
export async function setJobPriority(
	fetch: Fetch,
	jobId: string,
	priority: number,
	expectedFenceToken: number
): Promise<CommandResponse> {
	return parseCommandResponse(
		await apiSend(fetch, 'POST', `/jobs/${jobId}/priority`, {
			priority,
			expected_fence_token: expectedFenceToken
		})
	);
}

/** `POST /api/jobs/actions` — a bounded bulk command returning one result per job. */
export async function bulkJobActions(
	fetch: Fetch,
	request: BulkActionRequest
): Promise<BulkActionResponse> {
	const response = await apiSend<unknown>(fetch, 'POST', '/jobs/actions', request);
	if (
		typeof response !== 'object' ||
		response === null ||
		!Array.isArray((response as { items?: unknown }).items)
	) {
		throw new IncompatibleResponseError('bulk actions', response);
	}
	return response as BulkActionResponse;
}

// ---- multiplexed event stream ---------------------------------------------

/**
 * The minimal EventSource surface the store depends on, so tests can inject a
 * fake stream. It matches the browser `EventSource` (named-event listeners plus
 * `close`); the `data`/`lastEventId` fields are absent on `open`/`error` frames.
 */
export interface EventSourceLike {
	addEventListener(
		type: string,
		listener: (event: { data?: string; lastEventId?: string }) => void
	): void;
	close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

/** The single same-origin multiplexed SSE endpoint (through the API-key proxy). */
export const JOB_EVENT_STREAM_URL = '/api/jobs/events/stream';

/**
 * Build the EventSource URL, optionally resuming from a known durable cursor.
 * No credential ever appears here — the same-origin proxy attaches the API key
 * server-side (A06).
 */
export function jobEventStreamUrl(after?: string | number | null): string {
	if (after === undefined || after === null || after === '') {
		return JOB_EVENT_STREAM_URL;
	}
	return `${JOB_EVENT_STREAM_URL}?after=${encodeURIComponent(String(after))}`;
}
