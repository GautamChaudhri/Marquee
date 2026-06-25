import type { PageLoad } from './$types';
import { getRadarrOverlay } from '$lib/api/radarr-overlay';
import type {
	HdrKind,
	RadarrOverlayStatus,
	RadarrOverlayQuery
} from '$lib/api/types';

const PAGE_SIZE = 100;

export const load: PageLoad = async ({ fetch, url }) => {
	const sp = url.searchParams;
	const hdrTags = sp
		.getAll('hdr_tags')
		.flatMap((value) => value.split(','))
		.map((value) => value.trim())
		.filter(Boolean);
	const query: RadarrOverlayQuery = {
		page: Math.max(1, Number(sp.get('page') ?? '1') || 1),
		page_size: PAGE_SIZE,
		hdr: (sp.get('hdr') as HdrKind | 'unknown') || undefined,
		hdr_tags: hdrTags.length ? hdrTags : undefined,
		cf_score_min: sp.get('cf_score_min') ? Number(sp.get('cf_score_min')) : undefined,
		cf_score_max: sp.get('cf_score_max') ? Number(sp.get('cf_score_max')) : undefined,
		profile_id: sp.get('profile_id') ? Number(sp.get('profile_id')) : undefined,
		preference_status:
			(sp.get('preference_status') as RadarrOverlayStatus) || undefined,
		dovi_no_fallback:
			sp.get('dovi_no_fallback') === 'true' ? true : undefined,
		sort_by:
			(sp.get('sort_by') as 'title' | 'year' | 'cf_score' | 'preference_status') ||
			'cf_score',
		sort_dir: (sp.get('sort_dir') as 'asc' | 'desc') || 'desc'
	};

	try {
		const data = await getRadarrOverlay(fetch, query);
		return { data, query, error: null as string | null };
	} catch (e) {
		return {
			data: null,
			query,
			error: e instanceof Error ? e.message : 'Failed to load Radarr overlay'
		};
	}
};
