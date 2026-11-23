import type { PageLoad } from './$types';
import { getTvMetrics, getTvReviewQueue, getTvRunQueue, getTvSummary } from '$lib/api/pipeline-tv';
import type { PipelineMetrics, TvPipelineSummary, TvReviewQueue, TvRunQueue } from '$lib/api/types';

const EMPTY_SUMMARY: TvPipelineSummary = {
	shows_total: 0,
	shows_with_show_poster: 0,
	shows_missing_show_poster: 0,
	seasons_total: 0,
	seasons_with_poster: 0,
	seasons_missing_poster: 0,
	shows_fully_covered: 0,
	shows_in_review: 0,
	seasons_in_review: 0,
	assets_in_review: 0,
	assets_in_run: 0,
	shows_no_tmdb: 0,
	running_jobs: [],
	last_heal: null,
	heal_schedule: null,
	backups: { count: 0, bytes: 0 }
};

export const load: PageLoad = async ({ fetch }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);
	const [summary, runQueue, reviewQueue, metrics] = await Promise.all([
		safe<TvPipelineSummary>(getTvSummary(fetch), EMPTY_SUMMARY),
		safe<TvRunQueue>(getTvRunQueue(fetch), { items: [], total: 0 }),
		safe<TvReviewQueue>(getTvReviewQueue(fetch, { page_size: 200 }), {
			total_series: 0,
			page: 1,
			page_size: 200,
			items: []
		}),
		safe<PipelineMetrics | null>(getTvMetrics(fetch, { limit: 500 }), null)
	]);
	return { summary, runQueue, reviewQueue, metrics };
};
