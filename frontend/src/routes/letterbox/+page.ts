import type { PageLoad } from './$types';
import { getLetterboxSummary } from '$lib/api/letterbox';
import type { LetterboxSummaryResponse } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);

	const summary = await safe<LetterboxSummaryResponse | null>(getLetterboxSummary(fetch), null);

	return {
		summary,
		error: summary ? null : 'Could not reach the letterbox summary service.'
	};
};
