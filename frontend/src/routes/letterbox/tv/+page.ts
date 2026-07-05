import type { PageLoad } from './$types';
import { getLetterboxTv } from '$lib/api/letterbox';

export const load: PageLoad = async ({ fetch, url }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);

	const verdict = url.searchParams.get('verdict') || undefined;
	const uniformity = url.searchParams.get('uniformity') || undefined;
	const hasCandidatesStr = url.searchParams.get('has_candidates');
	const has_candidates =
		hasCandidatesStr === 'true' ? true : hasCandidatesStr === 'false' ? false : undefined;
	const q = url.searchParams.get('q') || undefined;

	const data = await safe(getLetterboxTv(fetch, { verdict, uniformity, has_candidates, q }), {
		total: 0,
		items: []
	});

	return {
		tvData: data,
		filters: {
			verdict,
			uniformity,
			has_candidates,
			q
		}
	};
};
