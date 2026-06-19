import type { PageLoad } from './$types';
import { getLetterboxStatus, listColumn } from '$lib/api/letterbox';
import type { LetterboxColumn, LetterboxStatus } from '$lib/api/types';

const CAP = 25;

const EMPTY: LetterboxColumn = { items: [], total: 0 };

/** Defensive split: if the backend `reviewed` param isn't live yet, both tagged
 *  queries return the same set — filter client-side so columns stay disjoint. */
function filterReviewed(col: LetterboxColumn, reviewed: boolean): LetterboxColumn {
	const items = col.items.filter((i) => Boolean(i.reviewed) === reviewed);
	return { items, total: items.length === col.items.length ? col.total : items.length };
}

export const load: PageLoad = async ({ fetch, url }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);
	const dsort = url.searchParams.get('dsort');
	const detectedDesc = dsort !== 'asc';

	const [status, candidates, detected, previewRaw, notLb, processedRaw] = await Promise.all([
		safe<LetterboxStatus | null>(getLetterboxStatus(fetch), null),
		safe(
			listColumn(fetch, {
				status: 'prefilter_candidate,prefilter_unknown',
				sort: 'confidence',
				page_size: CAP
			}),
			EMPTY
		),
		safe(
			listColumn(fetch, {
				status: 'candidate',
				sort: 'confidence',
				desc: detectedDesc,
				page_size: CAP
			}),
			EMPTY
		),
		safe(
			listColumn(fetch, {
				status: 'tagged',
				reviewed: false,
				sort: 'recent',
				page_size: CAP
			}),
			EMPTY
		),
		safe(
			listColumn(fetch, {
				status: 'not_letterboxed',
				sort: 'recent',
				page_size: CAP
			}),
			EMPTY
		),
		safe(
			listColumn(fetch, { status: 'tagged', reviewed: true, sort: 'recent', page_size: CAP }),
			EMPTY
		)
	]);

	const reachedBackend = status !== null;

	return {
		status,
		error: reachedBackend ? null : 'Could not reach the letterbox service.',
		detectedDesc,
		columns: {
			candidates,
			detected,
			preview: filterReviewed(previewRaw, false),
			notLetterboxed: notLb,
			processed: filterReviewed(processedRaw, true)
		}
	};
};
