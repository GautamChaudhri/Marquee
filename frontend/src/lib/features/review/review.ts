/** What the Review pages say: answers, events, confirmations, and what comes next. */
import type { FeedbackEvent, FeedbackListItem, PendingReview } from '#lib/api/types.ts';

type EventLike = Pick<
	FeedbackEvent,
	'action' | 'chosen_source_file' | 'context' | 'revokes_event_id' | 'revoked_by'
>;

function list(context: Record<string, unknown>, name: string): string[] {
	const value = context[name];
	return Array.isArray(value) ? value.map(String) : [];
}

function plural(n: number, one: string, many = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}

/** One line for an act of feedback. */
export function eventLine(event: EventLike): string {
	const chosen = event.chosen_source_file ?? 'a candidate';
	const hated = list(event.context ?? {}, 'hated').length;
	switch (event.action) {
		case 'approval':
			return `Kept the pick (${chosen})`;
		case 'override':
			return `Chose ${chosen}`;
		case 'rank': {
			const order = list(event.context ?? {}, 'order').length;
			return `Ranked ${plural(order, 'design')} with ${chosen} first; ${plural(hated, 'hated design')}`;
		}
		case 'reject':
			return `Refused every design; ${plural(hated, 'hated design')}`;
		case 'revoke':
			return `Undid answer ${event.revokes_event_id ?? ''}`.trim();
	}
}

/** Whether an event can still be undone: not a revoke, and not undone already. */
export function canUndo(event: Pick<EventLike, 'action' | 'revoked_by'>): boolean {
	return event.action !== 'revoke' && (event.revoked_by === null || event.revoked_by === undefined);
}

/** What the page says once an answer was recorded. `asked` is the action the page sent. */
export function recordedLine(
	event: Pick<FeedbackEvent, 'id' | 'action'> & Partial<EventLike>,
	status: number,
	asked: string
): string[] {
	const lines = [`Recorded as answer ${event.id}: ${eventLine({ ...blank, ...event })}.`];
	if (asked === 'override' && event.action === 'approval') {
		lines.push('The poster you chose is the pick, so it was recorded as keeping the pick.');
	}
	if (status === 200) lines.push('This answer had already been recorded; nothing changed.');
	return lines;
}

const blank: EventLike = {
	action: 'approval',
	chosen_source_file: null,
	context: {},
	revokes_event_id: null,
	revoked_by: null
};

/** What the page says about the folder after an answer. */
export function folderLine(
	action: string,
	deployAsked: boolean,
	deployJob: number | null | undefined,
	deployNote: string | null | undefined
): string {
	if (action === 'reject') return 'Nothing is written to the movie’s folder.';
	if (deployJob) return `Deploy job ${deployJob} writes the chosen poster to the movie’s folder.`;
	if (deployNote) return `No poster is written: ${deployNote}`;
	if (!deployAsked) return 'Nothing is written to the movie’s folder: you chose not to.';
	return 'No deploy was queued.';
}

/** The confirmation for "Save this ranking". */
export function rankConfirmation(first: string, hated: number, deploy: boolean): string {
	const folder = deploy
		? `${first} is written to the movie’s folder; what is there now is backed up first.`
		: `${first} becomes the movie’s choice; nothing is written to the folder.`;
	const pile =
		hated === 0
			? 'No design is hated.'
			: `The ${plural(hated, 'hated design')} become examples of what you do not want (those ranked 10 or better, at most 8 per movie).`;
	return `${folder} ${pile}`;
}

/** The confirmation for "None of these". */
export function rejectConfirmation(shown: number): string {
	return `Nothing is written to the movie’s folder. The ${plural(shown, 'design')} shown become examples of what you do not want (the best-ranked 8 of those ranked 10 or better).`;
}

/** The confirmation for "Keep the pick" or "Use this one" when it replaces the owner's own
 *  poster; null when no confirmation is needed. */
export function replaceConfirmation(
	chosen: string,
	deploy: boolean,
	posterOrigin: string | null | undefined
): string | null {
	if (!deploy || posterOrigin !== 'library') return null;
	return `This writes ${chosen} over your own poster in the movie’s folder. Your poster is backed up first and can be put back from the movie’s poster history.`;
}

/** The next run waiting for a review, other than this one. */
export function nextRun(pending: PendingReview[], current: number): PendingReview | null {
	return pending.find((item) => item.run_id !== current) ?? null;
}

/** A recent-feedback row's movie, as "Title (Year)". */
export function movieLabel(item: Pick<FeedbackListItem, 'title' | 'year'>): string {
	return item.year ? `${item.title} (${item.year})` : item.title;
}
