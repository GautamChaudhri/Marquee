import { apiGet, apiSend, type Fetch } from './client';

/** Progress block written by the job manager for parent/batch jobs. */
export interface JobProgress {
	children_total?: number;
	children_completed?: number;
	children_failed?: number;
}

/** Subset of the durable job record we need to re-attach a progress bar. */
export interface JobSnapshot {
	job_id: string;
	type: string;
	status: string;
	cancel_requested: boolean;
	progress: JobProgress | null;
	result: Record<string, unknown> | null;
	error?: { type?: string; message?: string } | null;
	events_url: string;
}

/** One row from `GET /jobs` (the job_summary shape) — every field
 *  `job_summary()` returns, not just the ones a progress bar needs. */
export interface JobListItem {
	job_id: string;
	type: string;
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
	result: Record<string, unknown> | null;
	error: { type?: string; message?: string } | null;
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

export function isTerminal(status: string): boolean {
	return TERMINAL.includes(status);
}

/** Fetch a durable job's current snapshot (used to re-hydrate UI after refresh). */
export function getJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return apiGet<JobSnapshot>(fetchFn, `/jobs/${jobId}`);
}

/** Fetch the full job detail (attempts, resource reservations, result/error). */
export function getJobDetail(fetchFn: Fetch, jobId: string): Promise<JobDetail> {
	return apiGet<JobDetail>(fetchFn, `/jobs/${jobId}`);
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
	return apiGet(fetchFn, '/jobs', params);
}

/** Job-platform-wide counts, resource-pool utilization, and worker health. */
export function getJobMetrics(fetchFn: Fetch): Promise<JobMetrics> {
	return apiGet<JobMetrics>(fetchFn, '/jobs/metrics');
}

/** Success rate + duration percentiles per job type. */
export function getJobMetricsByType(
	fetchFn: Fetch
): Promise<{ by_type: Record<string, JobTypeStats> }> {
	return apiGet(fetchFn, '/jobs/metrics/by-type');
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

export function cancelJob(
	fetchFn: Fetch,
	jobId: string
): Promise<{ job_id: string; status: string; cancel_requested: boolean }> {
	return apiSend(fetchFn, 'POST', `/jobs/${jobId}/cancel`, {});
}

export function pauseJob(fetchFn: Fetch, jobId: string): Promise<JobListItem> {
	return apiSend(fetchFn, 'POST', `/jobs/${jobId}/pause`, {});
}

export function resumeJob(fetchFn: Fetch, jobId: string): Promise<JobListItem> {
	return apiSend(fetchFn, 'POST', `/jobs/${jobId}/resume`, {});
}

export function retryJob(fetchFn: Fetch, jobId: string): Promise<JobListItem> {
	return apiSend(fetchFn, 'POST', `/jobs/${jobId}/retry`, {});
}
