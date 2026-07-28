import { describe, expect, it } from 'vitest';
import type { Fetch } from './client';
import { retrainTaste } from './taste';

type Recorded = { url: string; method: string; body: unknown };

function stub(body: unknown, recorded: Recorded[]): Fetch {
	return (async (url: unknown, init?: RequestInit) => {
		recorded.push({
			url: String(url),
			method: (init?.method ?? 'GET').toUpperCase(),
			body: init?.body ? JSON.parse(String(init.body)) : undefined
		});
		return new Response(JSON.stringify(body), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		});
	}) as unknown as Fetch;
}

describe('taste profile rebuild', () => {
	it('posts the selected library and lets the server pick the training source', async () => {
		const recorded: Recorded[] = [];
		await retrainTaste(stub({ job_id: 'taste-job' }, recorded), 'tv');

		expect(recorded).toEqual([
			{
				url: '/api/taste/retrain',
				method: 'POST',
				body: { library: 'tv' }
			}
		]);
	});
});
