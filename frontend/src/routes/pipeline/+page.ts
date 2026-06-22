import type { PageLoad } from './$types';
import { getPipelineCache, getPipelineMetrics, getReviewQueue } from '$lib/api/pipeline';
import { listMovies } from '$lib/api/library';
import { listJobs } from '$lib/api/jobs';
import type { JobListItem } from '$lib/api/jobs';
import type {
	CacheSizes,
	MovieListItem,
	Paginated,
	PipelineMetrics,
	ReviewQueue
} from '$lib/api/types';

const EMPTY_QUEUE: ReviewQueue = { total: 0, page: 1, page_size: 60, items: [] };
const EMPTY_MISSING: Paginated<MovieListItem> = { total: 0, page: 1, page_size: 60, items: [] };

export const load: PageLoad = async ({ fetch }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);
	const [queue, cache, metrics, missing, activeJob] = await Promise.all([
		safe<ReviewQueue>(getReviewQueue(fetch, { page_size: 60 }), EMPTY_QUEUE),
		safe<CacheSizes | null>(getPipelineCache(fetch), null),
		safe<PipelineMetrics | null>(getPipelineMetrics(fetch, { limit: 500 }), null),
		safe<Paginated<MovieListItem>>(
			listMovies(fetch, { poster_status: 'missing', sort: 'title', page_size: 60 }),
			EMPTY_MISSING
		),
		// Re-attach to a running batch so the progress bar survives refresh.
		safe<JobListItem | null>(
			listJobs(fetch, { type: 'poster_pipeline_batch', status: 'running', limit: 1 }).then(
				(r) => r.jobs[0] ?? null
			),
			null
		)
	]);
	return { queue, cache, metrics, missing, activeJob };
};
