import { apiSend, type Fetch } from './client';
import type { FeedbackRequestBody, FeedbackResult } from './types';

/** Approve the auto-pick, override with a chosen poster, or reject all. Writes
 *  pairwise labels, appends the pick to the taste profile, and (by default)
 *  deploys the chosen poster to the movie folder. */
export function submitFeedback(fetchFn: Fetch, body: FeedbackRequestBody): Promise<FeedbackResult> {
	const mutation =
		body.action !== 'reject_all' && body.deploy !== false && !body.idempotency_key
			? { ...body, idempotency_key: `poster_deploy:${crypto.randomUUID()}` }
			: body;
	return apiSend<FeedbackResult>(fetchFn, 'POST', '/feedback', mutation);
}

/** Undo a feedback submission by its event id (removes labels + exemplar). */
export function undoFeedback(
	fetchFn: Fetch,
	eventId: string
): Promise<{ removed_labels: number; exemplars_removed: string[] }> {
	return apiSend(fetchFn, 'POST', '/feedback/undo', { event_id: eventId });
}
