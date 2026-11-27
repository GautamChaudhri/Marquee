/** How the review queue is shown: cards with the current poster and the run's best designs, or a
 *  gallery of the picks alone. The choice is kept in this browser, so the queue opens the way
 *  it was left. */
import type { Candidate } from '#lib/api/types.ts';
import { persisted } from '#lib/stores/persisted.ts';
import { groupIntoCards, type RankedCandidate } from '#lib/features/review/panel.ts';

/** How many of a run's designs a card in the list holds, beside the current poster. A narrow
 *  card shows one fewer (QueueCard), so the posters stay a size that can be judged. */
export const TOP_DESIGNS = 6;

/** A run's best designs, best first, as the review page orders them: each design's
 *  best-ranked variant, at most `limit`. Fewer when the run ranked fewer designs. */
export function topDesigns(candidates: Candidate[], limit = TOP_DESIGNS): RankedCandidate[] {
	return groupIntoCards(candidates)
		.slice(0, limit)
		.map((card) => card.variants[0]!);
}

export type QueueView = 'list' | 'gallery';

export const QUEUE_VIEWS: readonly { value: QueueView; label: string; icon: string }[] = [
	{ value: 'list', label: 'List', icon: 'list' },
	{ value: 'gallery', label: 'Gallery', icon: 'grid' }
];

export const queueView = persisted<QueueView>('marquee:review-view', 'list');

/** The view a stored value names. Anything but `gallery` (an old or hand-edited value) is
 *  the list, which is how the queue first opens. */
export function asQueueView(value: unknown): QueueView {
	return value === 'gallery' ? 'gallery' : 'list';
}
