import { describe, expect, it } from 'vitest';
import type { Fetch } from '../api/client';
import {
	bulkJobActions,
	cancelJob,
	getPresentation,
	getRawDocument,
	getSnapshot,
	jobEventStreamUrl,
	JOB_EVENT_STREAM_URL,
	listArtifacts,
	listAttemptLogs,
	listAttempts,
	listChildren,
	listEvents,
	listJobs,
	pauseJob,
	resumeJob,
	retryJob,
	setJobPriority
} from './client';

type Recorded = { url: string; method: string; body: unknown };

const snapshot = {
	version: 1,
	job_id: 'j1',
	type: 'poster_pipeline',
	phase: 'running',
	outcome: null,
	desired_state: 'run',
	fence_token: 3,
	progress_sequence: 7,
	progress: null,
	links: {}
};

/** A recording fake fetch that returns a canned JSON body. */
function stub(body: unknown, recorded: Recorded[], status = 200): Fetch {
	return (async (url: unknown, init?: RequestInit) => {
		recorded.push({
			url: String(url),
			method: (init?.method ?? 'GET').toUpperCase(),
			body: init?.body ? JSON.parse(String(init.body)) : undefined
		});
		return new Response(JSON.stringify(body), {
			status,
			headers: { 'content-type': 'application/json' }
		});
	}) as unknown as Fetch;
}

const listBody = { items: [], limit: 50, next_cursor: null, view: 'queue' };
const commandBody = { action: 'cancel', execution_class: 'media_write', snapshot };

describe('read wrappers hit the canonical bounded routes', () => {
	it('listJobs serializes the query and validates the envelope', async () => {
		const rec: Recorded[] = [];
		const result = await listJobs(stub(listBody, rec), { view: 'queue', limit: 10 });
		expect(rec[0]).toMatchObject({ url: '/api/jobs?view=queue&limit=10', method: 'GET' });
		expect(result.items).toEqual([]);
	});

	it('getSnapshot validates the snapshot', async () => {
		const rec: Recorded[] = [];
		const result = await getSnapshot(stub(snapshot, rec), 'j1');
		expect(rec[0].url).toBe('/api/jobs/j1/snapshot');
		expect(result.job_id).toBe('j1');
	});

	it('exposes each bounded diagnostic resource on its own route', async () => {
		const rec: Recorded[] = [];
		await getPresentation(stub({ job_id: 'j1' }, rec), 'j1');
		await listAttempts(stub({ items: [], limit: 50, next_cursor: null }, rec), 'j1');
		await listEvents(stub({ items: [], limit: 50, next_cursor: null }, rec), 'j1', { cursor: '4' });
		await listChildren(stub({ items: [], limit: 50, next_cursor: null }, rec), 'j1');
		await listArtifacts(stub({ items: [], limit: 50, next_cursor: null }, rec), 'j1');
		await listAttemptLogs(stub({ lines: [], next_cursor: null }, rec), 'j1', 2);
		await getRawDocument(stub({ document: {} }, rec), 'j1', 'result');
		expect(rec.map((r) => r.url)).toEqual([
			'/api/jobs/j1/presentation',
			'/api/jobs/j1/attempts',
			'/api/jobs/j1/events?cursor=4',
			'/api/jobs/j1/children',
			'/api/jobs/j1/artifacts',
			'/api/jobs/j1/attempts/2/logs',
			'/api/jobs/j1/raw/result'
		]);
		expect(rec.every((r) => r.method === 'GET')).toBe(true);
	});
});

describe('command wrappers post the fence-guarded body', () => {
	it('cancel/pause/resume/retry post the expected fence token', async () => {
		for (const [fn, action] of [
			[cancelJob, 'cancel'],
			[pauseJob, 'pause'],
			[resumeJob, 'resume'],
			[retryJob, 'retry']
		] as const) {
			const rec: Recorded[] = [];
			await fn(stub(commandBody, rec), 'j1', 9);
			expect(rec[0]).toEqual({
				url: `/api/jobs/j1/${action}`,
				method: 'POST',
				body: { expected_fence_token: 9 }
			});
		}
	});

	it('setJobPriority PATCHes priority with the fence token', async () => {
		const rec: Recorded[] = [];
		await setJobPriority(stub(commandBody, rec), 'j1', 5, 9);
		expect(rec[0]).toEqual({
			url: '/api/jobs/j1/priority',
			method: 'PATCH',
			body: { priority: 5, expected_fence_token: 9 }
		});
	});

	it('bulkJobActions posts to the bulk route and validates the item list', async () => {
		const rec: Recorded[] = [];
		const result = await bulkJobActions(stub({ items: [] }, rec), { items: [] });
		expect(rec[0]).toMatchObject({ url: '/api/jobs/actions', method: 'POST' });
		expect(result.items).toEqual([]);
	});
});

describe('event stream url', () => {
	it('is the single multiplexed same-origin endpoint', () => {
		expect(JOB_EVENT_STREAM_URL).toBe('/api/jobs/events/stream');
		expect(jobEventStreamUrl()).toBe('/api/jobs/events/stream');
		expect(jobEventStreamUrl(null)).toBe('/api/jobs/events/stream');
	});

	it('resumes from a durable cursor', () => {
		expect(jobEventStreamUrl('128')).toBe('/api/jobs/events/stream?after=128');
	});
});
