import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { CandidateView } from '$lib/api/types';
import PosterCandidateTile from './PosterCandidateTile.svelte';

const candidate: CandidateView = {
	orig_filename: 'neutral.jpg',
	rank: 1,
	final_score: 0.987,
	poster_url: '/poster.jpg',
	contributions: { knn_sim: 0.9 },
	raw_features: null,
	normalized_features: null,
	gate_decision: 'passed',
	gate_reason: null,
	stage_reached: 'ranked',
	rejection_reason: null,
	rejection_label: null,
	rejection_explanation: null,
	ocr_evidence: null,
	dedup_kept: null,
	stack_id: null,
	stack_rank: null,
	stack_pos: null,
	stack_label: null,
	stack_size: null,
	stack_score: null
};

describe('PosterCandidateTile cold-start candidate', () => {
	it('does not render neutral display position as an auto-pick or score', () => {
		render(PosterCandidateTile, { props: { candidate, kind: 'candidate' } });

		const tile = screen.getByRole('button');
		expect(tile).toHaveTextContent('Candidate');
		expect(tile).not.toHaveTextContent('Rank 1');
		expect(tile).not.toHaveTextContent('0.987');
		expect(tile).not.toHaveClass('auto');
	});

	it('uses the persisted auto-pick flag instead of a candidate rank', async () => {
		const view = render(PosterCandidateTile, { props: { candidate, kind: 'ranked' } });

		expect(screen.getByRole('button')).not.toHaveClass('auto');

		await view.rerender({ candidate, kind: 'ranked', isPersistedAutoPick: true });
		expect(screen.getByRole('button')).toHaveClass('auto');
	});
});
