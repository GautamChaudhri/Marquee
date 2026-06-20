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

export function cancelJob(
	fetchFn: Fetch,
	jobId: string
): Promise<{ job_id: string; status: string; cancel_requested: boolean }> {
	return apiSend(fetchFn, 'POST', `/jobs/${jobId}/cancel`, {});
}
