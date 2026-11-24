import { describe, expect, it } from 'vitest';
import type { JobEvent } from '$lib/api/types';
import { activityFromParams, digestFeed, paramsFromActivity } from './feed';

function event(id: number, jobId: number, type: JobEvent['type']): JobEvent {
	return { id, job_id: jobId, at: '2026-11-24T12:00:00Z', type, message: null, data: null };
}

describe('the Activity address', () => {
	it('reads state, kind, page, and the open job, ignoring what is not allowed', () => {
		expect(
			activityFromParams(new URLSearchParams('state=failed&kind=library_sync&page=3&job=12'))
		).toEqual({ state: 'failed', kind: 'library_sync', page: 3, job: 12 });
		expect(activityFromParams(new URLSearchParams('state=lost&kind=rm -rf&page=0&job=-1'))).toEqual(
			{
				state: null,
				kind: null,
				page: 1,
				job: null
			}
		);
		expect(activityFromParams(new URLSearchParams('page=2.5&job=1e3'))).toEqual({
			state: null,
			kind: null,
			page: 1,
			job: null
		});
	});

	it('writes only what is set', () => {
		expect(paramsFromActivity({ state: null, kind: null, page: 1, job: null }).toString()).toBe('');
		expect(
			paramsFromActivity({ state: 'queued', kind: 'poster_run', page: 2, job: 9 }).toString()
		).toBe('state=queued&kind=poster_run&page=2&job=9');
	});
});

describe('the event feed, as a hint', () => {
	it('names each shown job something happened to, once', () => {
		const digest = digestFeed(
			[
				event(1, 5, 'started'),
				event(2, 7, 'progress'),
				event(3, 5, 'succeeded'),
				event(4, 9, 'progress')
			],
			[5, 7, 8]
		);
		expect(digest).toEqual({ reread: [5, 7], listChanged: false });
	});

	it('says to read the list again when a job it does not show was queued or retried', () => {
		expect(digestFeed([event(1, 11, 'queued')], [5]).listChanged).toBe(true);
		expect(digestFeed([event(1, 11, 'retried')], [5]).listChanged).toBe(true);
		expect(digestFeed([event(1, 11, 'succeeded')], [5]).listChanged).toBe(false);
		expect(digestFeed([], [5])).toEqual({ reread: [], listChanged: false });
	});
});
