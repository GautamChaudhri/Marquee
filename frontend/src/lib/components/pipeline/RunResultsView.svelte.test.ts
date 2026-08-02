import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { CandidateView, RunResults } from '$lib/api/types';
import RunResultsView from './RunResultsView.svelte';

const candidate: CandidateView = {
	orig_filename: 'survivor.jpg',
	rank: 1,
	final_score: null,
	poster_url: '/candidate.jpg',
	contributions: null,
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

function run(reviewMode: RunResults['review_mode'], ranked: CandidateView[]): RunResults {
	return {
		run_id: 'review-run',
		movie: { id: 1, title: 'Fixture film', tmdb_id: 10 },
		status: 'completed',
		scorer: reviewMode === 'personalized' ? 'taste-profile' : null,
		review_mode: reviewMode,
		reviewed: false,
		auto_pick: reviewMode === 'personalized' ? (ranked[0] ?? null) : null,
		ranked,
		stacks: [],
		rejected: { gate: [], ocr: [], dedup: [], errored: [] },
		rejected_by_stage: [],
		rejection_summary: {},
		suggestion: null,
		counts: { ranked: ranked.length },
		stage_timings_s: {},
		media_type: 'movie'
	};
}

function renderRun(reviewMode: RunResults['review_mode'], ranked: CandidateView[]) {
	return render(RunResultsView, {
		props: {
			data: {
				run: run(reviewMode, ranked),
				runId: 'review-run',
				debugMode: false,
				ocrLabelState: {
					run_id: 'review-run',
					labels: { false_rejection: [], false_acceptance: [] }
				},
				error: null
			}
		}
	});
}

describe('RunResultsView review notices', () => {
	it('shows one amber manual-review notice for neutral survivors', () => {
		renderRun('collecting', [candidate]);

		expect(screen.getByText('Manual review')).toBeVisible();
		expect(
			screen.getAllByText(
				'These candidates passed objective checks. Their display order is neutral, not a recommendation. Choose one to teach Movies taste.'
			)
		).toHaveLength(1);
		expect(screen.queryByText('No survivors')).not.toBeInTheDocument();
	});

	it('replaces an empty survivor grid with the red no-survivors state', () => {
		renderRun('collecting', []);

		expect(screen.getByText('No survivors')).toBeVisible();
		expect(
			screen.getByText(
				'No candidates survived the objective checks. Re-run this title to try again.'
			)
		).toBeVisible();
		expect(screen.queryByText('No posters in this group.')).not.toBeInTheDocument();
		expect(screen.queryByText('Manual review')).not.toBeInTheDocument();
	});

	it('keeps personalized survivors free of cold-start guidance', () => {
		renderRun('personalized', [candidate]);

		expect(screen.queryByText('Manual review')).not.toBeInTheDocument();
		expect(screen.queryByText('No survivors')).not.toBeInTheDocument();
	});
});
