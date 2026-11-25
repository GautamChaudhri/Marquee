/** The Activity page's address, and what the job event feed says to read again.
 *
 *  The feed is a hint, never the record: event ids are handed out when an event is written,
 *  not when it commits, so a poll can skip one. The page therefore re-reads each job the feed
 *  mentions, and re-reads the whole list on a timer whatever the feed says. */
import type { JobEvent, JobState } from '#lib/api/types.ts';
import { KIND_NAMES } from './jobs';

export const JOB_PAGE_SIZE = 25;
export const FEED_EVERY_MS = 2_000;
export const LIST_EVERY_MS = 15_000;
export const FEED_LIMIT = 500;
const STATES: readonly JobState[] = ['queued', 'running', 'succeeded', 'failed', 'cancelled'];

export interface ActivityQuery {
	state: JobState | null;
	kind: string | null;
	page: number;
	job: number | null;
}

function positive(value: string | null): number | null {
	if (value === null || !/^\d{1,10}$/.test(value)) return null;
	const n = Number(value);
	return n >= 1 ? n : null;
}

export function activityFromParams(params: URLSearchParams): ActivityQuery {
	const state = params.get('state');
	const kind = params.get('kind');
	return {
		state: STATES.includes(state as JobState) ? (state as JobState) : null,
		kind: kind !== null && KIND_NAMES.includes(kind) ? kind : null,
		page: positive(params.get('page')) ?? 1,
		job: positive(params.get('job'))
	};
}

export function paramsFromActivity(query: ActivityQuery): URLSearchParams {
	const params = new URLSearchParams();
	if (query.state) params.set('state', query.state);
	if (query.kind) params.set('kind', query.kind);
	if (query.page > 1) params.set('page', String(query.page));
	if (query.job) params.set('job', String(query.job));
	return params;
}

export interface FeedDigest {
	/** Jobs on the page (or open in detail) that something happened to: read each again. */
	reread: number[];
	/** A job the page does not show was queued or retried: read the list again. */
	listChanged: boolean;
}

export function digestFeed(events: JobEvent[], shownIds: Iterable<number>): FeedDigest {
	const shown = new Set(shownIds);
	const reread = new Set<number>();
	let listChanged = false;
	for (const event of events) {
		if (shown.has(event.job_id)) reread.add(event.job_id);
		else if (event.type === 'queued' || event.type === 'retried') listChanged = true;
	}
	return { reread: [...reread].sort((a, b) => a - b), listChanged };
}
