import type { PipelineRunSummary, TvReviewGroup, TvRunQueueItem } from '$lib/api/types';
import { describe, expect, it } from 'vitest';
import { reviewPosterPreviews, resolveTvPosterTab, runPosterPlaceholders } from './tv-display';

function run(runId: string): PipelineRunSummary {
	return {
		run_id: runId,
		status: 'completed',
		started_at: null,
		completed_at: null,
		scorer_name: null,
		counts: null,
		reviewed: false
	};
}

describe('resolveTvPosterTab', () => {
	it('uses Review only when Run is empty and Review has shows', () => {
		expect(resolveTvPosterTab(null, 0, 1)).toBe('review');
		expect(resolveTvPosterTab(null, 1, 1)).toBe('run');
		expect(resolveTvPosterTab(null, 0, 0)).toBe('run');
	});

	it('preserves valid URL tabs and treats invalid ones as the default', () => {
		expect(resolveTvPosterTab('metrics', 0, 2)).toBe('metrics');
		expect(resolveTvPosterTab('run', 0, 2)).toBe('run');
		expect(resolveTvPosterTab('not-a-tab', 0, 2)).toBe('review');
	});
});

describe('TV poster tile display data', () => {
	it('returns the show followed by every season run in season order', () => {
		const item: TvReviewGroup = {
			series: { id: 1, title: 'Example', year: 2024, tmdb_id: 123 },
			show_run: { ...run('show'), auto_pick_poster_url: '/show.jpg' },
			season_runs: [
				{
					season_number: 2,
					season_id: 12,
					run: run('season-two'),
					auto_pick_poster_url: '/season-two.jpg',
					flagged_no_candidates: false
				},
				{
					season_number: 1,
					season_id: 11,
					run: run('season-one'),
					auto_pick_poster_url: '/season-one.jpg',
					flagged_no_candidates: false
				},
				{
					season_number: 3,
					season_id: 13,
					run: run('season-three'),
					auto_pick_poster_url: null,
					flagged_no_candidates: true
				}
			],
			seasons_only: false,
			display_poster_url: '/show.jpg'
		};

		expect(reviewPosterPreviews(item)).toEqual([
			{ label: 'Show', url: '/show.jpg' },
			{ label: 'S01', url: '/season-one.jpg' },
			{ label: 'S02', url: '/season-two.jpg' },
			{ label: 'S03', url: null }
		]);
	});

	it('keeps a labelled tile for a season whose run picked nothing', () => {
		const item: TvReviewGroup = {
			series: { id: 2, title: 'Devs', year: 2020, tmdb_id: 456 },
			show_run: null,
			season_runs: [
				{
					season_number: 1,
					season_id: 4,
					run: { ...run('season-one'), status: 'flagged_manual' },
					auto_pick_poster_url: null,
					flagged_no_candidates: true
				}
			],
			seasons_only: true,
			display_poster_url: '/api/library/series/2/poster'
		};

		expect(reviewPosterPreviews(item)).toEqual([{ label: 'S01', url: null }]);
	});

	it('uses the series title only for the show placeholder', () => {
		const item: TvRunQueueItem = {
			series: { id: 1, title: 'Example', year: 2024, tmdb_id: 123, poster_url: null },
			show_poster_missing: true,
			missing_seasons: [
				{ season_id: 12, number: 2, episode_file_count: 8 },
				{ season_id: 11, number: 1, episode_file_count: 8 }
			],
			assets_to_run: [],
			no_tmdb: false
		};

		expect(runPosterPlaceholders(item)).toEqual([
			{
				label: 'Show',
				title: 'Example',
				year: 2024,
				gradientKey: 'Example',
				centerTitle: false
			},
			{
				label: 'S01',
				title: 'S01',
				year: null,
				gradientKey: 'Example',
				centerTitle: true
			},
			{
				label: 'S02',
				title: 'S02',
				year: null,
				gradientKey: 'Example',
				centerTitle: true
			}
		]);
	});
});
