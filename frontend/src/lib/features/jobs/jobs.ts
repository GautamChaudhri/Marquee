/** What the pages say about a job, and following one until it ends. */
import { api, call } from '$lib/api/client';
import type { Job, JobState } from '$lib/api/types';
import { poll } from '$lib/poll';

export const FINAL_STATES: readonly JobState[] = ['succeeded', 'failed', 'cancelled'];
export const ACTIVE_STATES: readonly JobState[] = ['queued', 'running'];

export function isFinal(state: JobState): boolean {
	return FINAL_STATES.includes(state);
}

/** The API's own rules for the two buttons, as the routes enforce them. */
export function canCancel(job: Pick<Job, 'state' | 'cancel_requested'>): boolean {
	return job.state === 'queued' || (job.state === 'running' && !job.cancel_requested);
}

export function canRetry(job: Pick<Job, 'state'>): boolean {
	return job.state === 'failed' || job.state === 'cancelled';
}

const KIND_LABELS: Record<string, string> = {
	library_sync: 'Library sync',
	poster_run: 'Run',
	poster_deploy: 'Deploy',
	poster_restore: 'Restore',
	taste_rebuild: 'Taste rebuild',
	gpu_selftest: 'GPU self-test',
	batch: 'Batch'
};

/** Every job kind the API knows, for the Activity page's filter. */
export const KIND_NAMES: readonly string[] = Object.keys(KIND_LABELS);

export function kindLabel(kind: string): string {
	return KIND_LABELS[kind] ?? kind.replaceAll('_', ' ');
}

/** One line for a job's state: its progress while it runs, its error when it failed, and
 *  what it skipped when it stepped aside. */
export function describeJob(
	job: Pick<Job, 'state' | 'progress' | 'error' | 'result' | 'cancel_requested'>
): string {
	switch (job.state) {
		case 'queued':
			return 'Queued';
		case 'running': {
			if (job.cancel_requested) return 'Stopping…';
			const p = job.progress;
			const count = p && p.total ? `${p.current ?? 0}/${p.total}` : '';
			const parts = [count, p?.message ?? ''].filter(Boolean).join(' · ');
			return parts ? `Running · ${parts}` : 'Running';
		}
		case 'succeeded': {
			const skipped =
				job.result && typeof job.result === 'object' && 'skipped' in job.result
					? (job.result as { skipped: unknown }).skipped
					: null;
			return typeof skipped === 'string' && skipped ? `Done · skipped: ${skipped}` : 'Done';
		}
		case 'failed':
			return job.error ? `Failed · ${job.error}` : 'Failed';
		case 'cancelled':
			return 'Cancelled';
	}
}

/** Read a job every `intervalMs` until it reaches a final state; `onUpdate` sees each read,
 *  `onError` each failed read. Returns a function that stops following it. */
export function followJob(
	jobId: number,
	onUpdate: (job: Job) => void,
	onError: (message: string) => void = () => {},
	intervalMs = 2000
): () => void {
	let stop: () => void = () => {};
	stop = poll(async () => {
		const result = await call(
			api.GET('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } })
		);
		if (!result.ok) {
			onError(result.message);
			return;
		}
		onUpdate(result.data);
		if (isFinal(result.data.state)) stop();
	}, intervalMs);
	return () => stop();
}
