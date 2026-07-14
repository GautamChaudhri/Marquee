import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';

type CanonicalSnapshot = components['schemas']['JobSnapshotResponse'];
type CanonicalRow = components['schemas']['JobRow'];
type CanonicalCommand = components['schemas']['CommandResponse'];
type CanonicalPresentation = components['schemas']['JobPresentation'];
type CanonicalAttempts = components['schemas']['AttemptListResponse'];
type CanonicalEvents = components['schemas']['EventListResponse'];
type CanonicalChildren = components['schemas']['ChildListResponse'];

/** Progress block written by the job manager for parent, batch, and media bridge jobs. */
export interface JobProgress {
	children_total?: number;
	children_completed?: number;
	children_failed?: number;
	percent?: number;
	stage?: string;
}

export type JobContext = Record<string, unknown> | unknown[] | string | number | boolean | null;

/** Subset of the durable job record we need to re-attach a progress bar. */
export interface JobSnapshot {
	job_id: string;
	type: string;
	label?: string;
	status: string;
	fence_token: number;
	cancel_requested: boolean;
	progress: JobProgress | null;
	result: JobContext | null;
	error?: JobContext | null;
	events_url: string;
}

/** One row from `GET /jobs` (the job_summary shape) — every field
 *  `job_summary()` returns, not just the ones a progress bar needs. */
export interface JobListItem {
	job_id: string;
	type: string;
	label?: string;
	status: string;
	priority: number;
	parent_id: string | null;
	root_id: string | null;
	correlation_id: string | null;
	subject: { type: string; id: string; title: string | null } | null;
	stage: string | null;
	progress: JobProgress | null;
	resource_request: Record<string, number>;
	attempt_count: number;
	max_attempts: number;
	cancel_requested: boolean;
	pause_requested: boolean;
	scheduled_at: string | null;
	claimed_at: string | null;
	started_at: string | null;
	finished_at: string | null;
	created_at: string | null;
	status_url: string;
	events_url: string;
}

/** Full `GET /jobs/{id}` detail — `JobListItem` plus attempt history,
 *  resource-reservation timeline, and the handler's payload/result/error. */
export interface JobDetail extends JobListItem {
	payload: Record<string, unknown>;
	checkpoint: Record<string, unknown> | null;
	request: JobContext | null;
	plan: JobContext | null;
	result: JobContext | null;
	error: JobContext | null;
	media_job_id: string | null;
	attempts: {
		number: number;
		status: string;
		worker_id: string;
		started_at: string | null;
		finished_at: string | null;
		metrics: Record<string, unknown> | null;
		error: Record<string, unknown> | null;
	}[];
	resources: {
		key: string;
		units: number;
		stage: string | null;
		acquired_at: string | null;
		released_at: string | null;
	}[];
	events: {
		id: number;
		stage: string | null;
		state: string;
		message: string | null;
		detail: Record<string, unknown> | null;
		created_at: string | null;
	}[];
	children: JobChildDetail[];
}

export interface JobChildDetail extends JobListItem {
	payload: Record<string, unknown>;
	checkpoint: Record<string, unknown> | null;
	request: JobContext | null;
	plan: JobContext | null;
	result: JobContext | null;
	error: JobContext | null;
	media_job_id: string | null;
}

export interface ResourcePoolStatus {
	key: string;
	capacity: number;
	in_use: number;
	enabled: boolean;
}

export interface WorkerStatus {
	id: string;
	status: string;
	heartbeat_at: string;
}

export interface JobMetrics {
	counts: Record<string, number>;
	resources: ResourcePoolStatus[];
	workers: WorkerStatus[];
}

export interface JobTypeStats {
	sample_size: number;
	success_rate: number;
	duration_seconds: { avg: number | null; p50: number | null; p95: number | null };
}

const TERMINAL = ['succeeded', 'failed', 'cancelled', 'interrupted', 'dead_letter'];

function progressFromCanonical(
	progress: components['schemas']['CompactProgress'] | null | undefined
): JobProgress | null {
	if (!progress) return null;
	return {
		percent: progress.overall?.percent ?? undefined,
		stage: progress.stage_label ?? progress.stage_key ?? undefined
	};
}

function rowFromCanonical(row: CanonicalRow): JobListItem {
	return {
		job_id: row.job_id,
		type: row.job_type,
		label: row.label,
		status: row.status.outcome ?? row.status.phase,
		priority: row.priority,
		parent_id: row.parent_id ?? null,
		root_id: row.root_id ?? null,
		correlation_id: null,
		subject: {
			type: row.subject.kind,
			id: row.subject.display_id,
			title: row.subject.display_name
		},
		stage: row.progress?.stage_key ?? null,
		progress: progressFromCanonical(row.progress),
		resource_request: {},
		attempt_count: 0,
		max_attempts: 0,
		cancel_requested: !row.allowed_actions.includes('cancel') && row.status.phase === 'stopping',
		pause_requested: row.allowed_actions.includes('resume'),
		scheduled_at: row.eligible_at ?? null,
		claimed_at: null,
		started_at: row.started_at ?? null,
		finished_at: row.terminal_at ?? null,
		created_at: row.created_at ?? null,
		status_url: row.links.snapshot,
		events_url: row.links.snapshot
	};
}

export function isTerminal(status: string): boolean {
	return TERMINAL.includes(status);
}

/** Fetch a durable job's current snapshot (used to re-hydrate UI after refresh). */
export function getJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return apiGet<CanonicalSnapshot>(fetchFn, `/jobs/${jobId}/snapshot`).then((snapshot) => ({
		job_id: snapshot.job_id,
		type: snapshot.type,
		label: snapshot.label,
		status: snapshot.outcome ?? snapshot.phase,
		fence_token: snapshot.fence_token,
		cancel_requested: snapshot.desired_state === 'cancel',
		progress: progressFromCanonical(snapshot.progress),
		result: null,
		events_url: snapshot.links.snapshot
	}));
}

/** Fetch the full job detail (attempts, resource reservations, result/error). */
export async function getJobDetail(fetchFn: Fetch, jobId: string): Promise<JobDetail> {
	const raw = async (kind: 'request' | 'plan' | 'result' | 'error'): Promise<JobContext | null> => {
		try {
			return (await apiGet<{ document: JobContext }>(fetchFn, `/jobs/${jobId}/raw/${kind}`))
				.document;
		} catch {
			return null;
		}
	};
	const [snapshot, presentation, attempts, events, children, request, plan, result, error] =
		await Promise.all([
			apiGet<CanonicalSnapshot>(fetchFn, `/jobs/${jobId}/snapshot`),
			apiGet<CanonicalPresentation>(fetchFn, `/jobs/${jobId}/presentation`),
			apiGet<CanonicalAttempts>(fetchFn, `/jobs/${jobId}/attempts`),
			apiGet<CanonicalEvents>(fetchFn, `/jobs/${jobId}/events`),
			apiGet<CanonicalChildren>(fetchFn, `/jobs/${jobId}/children`),
			raw('request'),
			raw('plan'),
			raw('result'),
			raw('error')
		]);
	const base = rowFromCanonical({
		version: 1,
		job_id: snapshot.job_id,
		job_type: snapshot.type,
		label: snapshot.label,
		label_key: `jobs.${snapshot.type}`,
		feature_area: presentation.feature_area,
		presentation_family: presentation.presentation_family,
		subject: presentation.subject,
		action_headline: presentation.action.headline,
		status: snapshot.status,
		attention: snapshot.attention,
		trigger: presentation.trigger,
		progress: snapshot.progress,
		impact: presentation.impact,
		allowed_actions: snapshot.allowed_actions,
		evidence: presentation.evidence,
		priority: snapshot.priority,
		queue_rank: null,
		eligible_at: snapshot.eligible_at,
		created_at: snapshot.created_at,
		started_at: snapshot.started_at,
		terminal_at: snapshot.terminal_at,
		duration_seconds:
			snapshot.started_at && snapshot.terminal_at
				? (Date.parse(snapshot.terminal_at) - Date.parse(snapshot.started_at)) / 1000
				: null,
		parent_id: snapshot.parent_id,
		root_id: snapshot.root_id,
		retry_of_job_id: snapshot.retry_of_job_id,
		is_parent: children.items.length > 0,
		links: snapshot.links
	});
	return {
		...base,
		payload: (request && typeof request === 'object' && !Array.isArray(request)
			? request
			: {}) as Record<string, unknown>,
		checkpoint: null,
		request,
		plan,
		result,
		error,
		media_job_id: null,
		attempt_count: attempts.items.length,
		attempts: attempts.items.map((attempt) => ({
			number: attempt.number,
			status: attempt.outcome ?? attempt.phase,
			worker_id: attempt.worker_node_id ?? '',
			started_at: attempt.started_at ?? null,
			finished_at: attempt.finished_at ?? null,
			metrics: attempt.metrics ?? null,
			error: attempt.error ?? null
		})),
		resources: [],
		events: events.items.map((event) => ({
			id: event.id,
			stage: event.event_key,
			state: event.state,
			message: event.message ?? null,
			detail: event.detail ?? null,
			created_at: event.created_at ?? null
		})),
		children: children.items.map((child) => ({
			...rowFromCanonical(child),
			payload: {},
			checkpoint: null,
			request: null,
			plan: null,
			result: null,
			error: null,
			media_job_id: null
		}))
	};
}

export function getJobChildren(
	fetchFn: Fetch,
	jobId: string
): Promise<{ children: JobChildDetail[] }> {
	return apiGet<CanonicalChildren>(fetchFn, `/jobs/${jobId}/children`).then((response) => ({
		children: response.items.map(rowFromCanonical) as JobChildDetail[]
	}));
}

/** List jobs, optionally filtered (e.g. the running children of a batch).
 *  `active`/`queued_only` are shorthands over the manager's real status
 *  sets — "currently running" or "currently queued" isn't one status
 *  string, so prefer these over guessing a `status` literal. */
export function listJobs(
	fetchFn: Fetch,
	params: {
		parent_id?: string;
		status?: string;
		type?: string;
		subject_type?: string;
		subject_id?: string;
		correlation_id?: string;
		active?: boolean;
		queued_only?: boolean;
		before?: number;
		since?: number;
		until?: number;
		limit?: number;
	} = {}
): Promise<{ jobs: JobListItem[]; next_before: number | null }> {
	const phases = new Set(['planned', 'queued', 'running', 'stopping']);
	const phase = params.status && phases.has(params.status) ? params.status : undefined;
	const view = phase || params.active || params.queued_only ? 'queue' : 'history';
	return apiGet<components['schemas']['JobListResponse']>(fetchFn, '/jobs', {
		view,
		parent_id: params.parent_id,
		type: params.type,
		subject_kind: params.subject_type,
		subject_id: params.subject_id,
		correlation_id: params.correlation_id,
		phase,
		created_after: params.since ? new Date(params.since * 1000).toISOString() : undefined,
		created_before: params.until
			? new Date(params.until * 1000).toISOString()
			: params.before
				? new Date(params.before * 1000).toISOString()
				: undefined,
		limit: params.limit
	}).then((response) => ({ jobs: response.items.map(rowFromCanonical), next_before: null }));
}

/** Job-platform-wide counts, resource-pool utilization, and worker health. */
export async function getJobMetrics(fetchFn: Fetch): Promise<JobMetrics> {
	const [queue, history] = await Promise.all([
		apiGet<components['schemas']['JobListResponse']>(fetchFn, '/jobs', {
			view: 'queue',
			limit: 200
		}),
		apiGet<components['schemas']['JobListResponse']>(fetchFn, '/jobs', {
			view: 'history',
			limit: 200
		})
	]);
	const counts: Record<string, number> = {};
	for (const job of [...queue.items, ...history.items]) {
		const status = job.status.outcome ?? job.status.phase;
		counts[status] = (counts[status] ?? 0) + 1;
	}
	return { counts, resources: [], workers: [] };
}

/** Success rate + duration percentiles per job type. */
export async function getJobMetricsByType(
	fetchFn: Fetch
): Promise<{ by_type: Record<string, JobTypeStats> }> {
	const history = await apiGet<components['schemas']['JobListResponse']>(fetchFn, '/jobs', {
		view: 'history',
		limit: 200
	});
	const grouped = new Map<string, CanonicalRow[]>();
	for (const job of history.items)
		grouped.set(job.job_type, [...(grouped.get(job.job_type) ?? []), job]);
	const by_type: Record<string, JobTypeStats> = {};
	for (const [type, jobs] of grouped) {
		const succeeded = jobs.filter((job) => job.status.outcome === 'succeeded').length;
		by_type[type] = {
			sample_size: jobs.length,
			success_rate: jobs.length ? succeeded / jobs.length : 0,
			duration_seconds: { avg: null, p50: null, p95: null }
		};
	}
	return { by_type };
}

/** The movie currently being analyzed by a batch, for the per-card scan indicator.
 *  Returns `null` when no child is running. Subject ids are stored as strings. */
export async function runningChildMovieId(
	fetchFn: Fetch,
	parentId: string
): Promise<number | null> {
	try {
		const { jobs } = await listJobs(fetchFn, { parent_id: parentId, status: 'running', limit: 1 });
		const id = jobs[0]?.subject?.id;
		return id != null && id !== '' ? Number(id) : null;
	} catch {
		return null;
	}
}

async function runCommand(
	fetchFn: Fetch,
	jobId: string,
	action: 'cancel' | 'pause' | 'resume' | 'retry' | 'priority',
	body: Record<string, unknown> = {}
): Promise<JobSnapshot> {
	const current = await apiGet<CanonicalSnapshot>(fetchFn, `/jobs/${jobId}/snapshot`);
	const response = await apiSend<CanonicalCommand>(
		fetchFn,
		action === 'priority' ? 'PATCH' : 'POST',
		`/jobs/${jobId}/${action}`,
		{
			...body,
			expected_fence_token: current.fence_token
		}
	);
	const snapshot = response.snapshot;
	return {
		job_id: snapshot.job_id,
		type: snapshot.type,
		label: snapshot.label,
		status: snapshot.outcome ?? snapshot.phase,
		fence_token: snapshot.fence_token,
		cancel_requested: snapshot.desired_state === 'cancel',
		progress: progressFromCanonical(snapshot.progress),
		result: null,
		events_url: snapshot.links.snapshot
	};
}

export function cancelJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return runCommand(fetchFn, jobId, 'cancel');
}

export function pauseJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return runCommand(fetchFn, jobId, 'pause');
}

export function resumeJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return runCommand(fetchFn, jobId, 'resume');
}

export function retryJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return runCommand(fetchFn, jobId, 'retry');
}

export function setJobPriority(
	fetchFn: Fetch,
	jobId: string,
	priority: number
): Promise<JobSnapshot> {
	return runCommand(fetchFn, jobId, 'priority', { priority });
}
