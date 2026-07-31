import type { PageLoad } from './$types';
import { getPipelineMetrics, getReviewQueue } from '$lib/api/pipeline';
import { getOnboardingStatus, type OnboardingStatus } from '$lib/api/onboarding';
import { listMovies } from '$lib/api/library';
import type { MovieListItem, Paginated, PipelineMetrics, ReviewQueue } from '$lib/api/types';

const EMPTY_QUEUE: ReviewQueue = { total: 0, page: 1, page_size: 60, items: [] };
const EMPTY_MISSING: Paginated<MovieListItem> = { total: 0, page: 1, page_size: 60, items: [] };
const PAGE_SIZE = 60;

export const load: PageLoad = async ({ fetch }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);
	const [queue, metrics, missing, onboarding] = await Promise.all([
		safe<ReviewQueue>(getReviewQueue(fetch, { page_size: PAGE_SIZE }), EMPTY_QUEUE),
		safe<PipelineMetrics | null>(getPipelineMetrics(fetch, { limit: 500 }), null),
		safe<Paginated<MovieListItem>>(
			listMovies(fetch, {
				poster_status: 'missing',
				sort: 'title',
				page_size: PAGE_SIZE,
				exclude_in_review: true
			}),
			EMPTY_MISSING
		),
		safe<OnboardingStatus | null>(getOnboardingStatus(fetch), null)
	]);
	return { queue, metrics, missing, onboarding };
};
