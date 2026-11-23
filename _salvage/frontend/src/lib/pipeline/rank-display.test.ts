import { describe, expect, it } from 'vitest';
import type { CandidateView } from '$lib/api/types';
import { flatDisplayRanks, rankCaption } from './rank-display';

function candidate(orig_filename: string, stack_id: number | null = null): CandidateView {
	return {
		orig_filename,
		rank: null,
		final_score: null,
		poster_url: '/poster.jpg',
		contributions: null,
		raw_features: null,
		normalized_features: null,
		gate_decision: null,
		gate_reason: null,
		stage_reached: null,
		rejection_reason: null,
		rejection_label: null,
		rejection_explanation: null,
		ocr_evidence: null,
		dedup_kept: null,
		stack_id,
		stack_rank: null,
		stack_pos: null,
		stack_label: null,
		stack_size: null,
		stack_score: null
	};
}

describe('flatDisplayRanks', () => {
	const first = candidate('first.jpg');
	const stackA = { ...candidate('stack-a.jpg', 12), stack_pos: 1, stack_label: 'A' };
	const stackB = { ...candidate('stack-b.jpg', 12), stack_pos: 2, stack_label: 'B' };
	const last = candidate('last.jpg');
	const items = [first, [stackA, stackB], last];

	it('numbers collapsed stack cards by their visible grid position', () => {
		expect([...flatDisplayRanks(items, [])]).toEqual([
			['first.jpg', { rank: 1, suffix: null }],
			['stack-a.jpg', { rank: 2, suffix: null }],
			['last.jpg', { rank: 3, suffix: null }]
		]);
	});

	it('keeps a stack rank and adds variants when it is expanded', () => {
		expect([...flatDisplayRanks(items, [12])]).toEqual([
			['first.jpg', { rank: 1, suffix: null }],
			['stack-a.jpg', { rank: 2, suffix: 'A' }],
			['stack-b.jpg', { rank: 2, suffix: 'B' }],
			['last.jpg', { rank: 3, suffix: null }]
		]);
	});

	it('uses an explicit rank caption', () => {
		expect(rankCaption(2)).toBe('Rank 2');
		expect(rankCaption(2, 'A')).toBe('Rank 2A');
		expect(rankCaption(null)).toBe('');
	});
});
