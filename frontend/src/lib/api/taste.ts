import { apiGet, apiSend, type Fetch } from './client';
import type { JobSummary, TasteSource, TasteStatus } from './types';

/** Taste-profile + learned-head ("Key Art Engine") status, labels, exemplars. */
export function getTasteStatus(fetchFn: Fetch): Promise<TasteStatus> {
	return apiGet<TasteStatus>(fetchFn, '/taste/status');
}

/** Rebuild the taste profile (initial training). `training_dir` (default) uses
 *  the curated folder; `library` rebuilds from every deployed poster. */
export function retrainTaste(
	fetchFn: Fetch,
	source: TasteSource = 'training_dir'
): Promise<JobSummary> {
	return apiSend<JobSummary>(fetchFn, 'POST', '/taste/retrain', { source });
}

/** Train the learned head (UI "Key Art Engine") from accumulated labels. */
export function retrainHead(fetchFn: Fetch): Promise<JobSummary> {
	return apiSend<JobSummary>(fetchFn, 'POST', '/taste/head/retrain');
}

/** Request cancellation of a running taste-profile rebuild. */
export function cancelRetrain(
	fetchFn: Fetch
): Promise<{ status: string } & Record<string, unknown>> {
	return apiSend(fetchFn, 'POST', '/taste/retrain/cancel', {});
}
