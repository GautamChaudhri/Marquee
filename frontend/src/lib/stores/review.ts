/** How many runs wait for a review, for the navigation's Review entry. It is read when the
 *  shell loads and again after each answer or undo. */
import { writable } from 'svelte/store';
import { api, call } from '#lib/api/client.ts';

export const pendingReviews = writable<number | null>(null);

export async function refreshPendingReviews(): Promise<void> {
	const result = await call(api.GET('/api/taste'));
	if (result.ok) pendingReviews.set(result.data.pending_reviews);
}
