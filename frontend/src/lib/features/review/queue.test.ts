import { describe, expect, it } from 'vitest';
import type { Candidate } from '#lib/api/types.ts';
import { asQueueView, QUEUE_VIEWS, topDesigns } from './queue';

describe('the review queue’s view', () => {
	it('is the gallery only when the gallery was chosen', () => {
		expect(asQueueView('gallery')).toBe('gallery');
		expect(asQueueView('list')).toBe('list');
		for (const other of [null, undefined, '', 'grid', 'Gallery', 1, {}]) {
			expect(asQueueView(other)).toBe('list');
		}
	});

	it('offers the list first, then the gallery', () => {
		expect(QUEUE_VIEWS.map((view) => view.value)).toEqual(['list', 'gallery']);
	});
});

describe('a run’s top designs', () => {
	const candidate = (id: number, rank: number | null, stack: string | null) =>
		({ id, rank, stack, image_url: `/img/${id}`, sha256: 'x' }) as unknown as Candidate;

	it('takes each design’s best-ranked variant, in rank order, at most six', () => {
		const candidates = [
			candidate(1, 1, '1A'),
			candidate(2, 2, '2A'),
			candidate(3, 3, '1B'),
			candidate(4, 4, '3A'),
			candidate(5, 5, null),
			candidate(6, 6, '4A'),
			candidate(7, 7, '5A'),
			candidate(8, 8, '6A'),
			candidate(9, null, '7A')
		];
		expect(topDesigns(candidates).map((c) => c.id)).toEqual([1, 2, 4, 5, 6, 7]);
		expect(topDesigns(candidates, 2).map((c) => c.id)).toEqual([1, 2]);
	});

	it('gives fewer when the run ranked fewer, and none when it ranked none', () => {
		expect(topDesigns([candidate(1, 1, '1A'), candidate(2, 2, '1B')]).map((c) => c.id)).toEqual([
			1
		]);
		expect(topDesigns([candidate(1, null, null)])).toEqual([]);
	});
});
