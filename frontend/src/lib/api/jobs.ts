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

/** One row from `GET /jobs` (the job_summary shape). */
export interface JobListItem {
	job_id: string;
	type: string;
	status: string;
	subject: { type: string; id: string } | null;
	stage: string | null;
	progress: JobProgress | null;
	events_url: string;
}

const TERMINAL = ['succeeded', 'failed', 'cancelled', 'interrupted', 'dead_letter'];

export function isTerminal(status: string): boolean {
	return TERMINAL.includes(status);
}

/** Fetch a durable job's current snapshot (used to re-hydrate UI after refresh). */
export function getJob(fetchFn: Fetch, jobId: string): Promise<JobSnapshot> {
	return apiGet<JobSnapshot>(fetchFn, `/jobs/${jobId}`);
}

/** List jobs, optionally filtered (e.g. the running children of a batch). */
export function listJobs(
	fetchFn: Fetch,
	params: { parent_id?: string; status?: string; type?: string; limit?: number } = {}
): Promise<{ jobs: JobListItem[]; next_before: number | null }> {
	return apiGet(fetchFn, '/jobs', params);
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
