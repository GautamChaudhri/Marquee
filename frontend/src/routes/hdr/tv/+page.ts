import type { PageLoad } from './$types';
import { getHdrTv } from '$lib/api/hdr';
import type { ShowStatus, ShowUniformity, HdrTvQuery } from '$lib/api/types';

const PAGE_SIZE = 100;

export const load: PageLoad = async ({ fetch, url }) => {
	const sp = url.searchParams;
	const hdrTags = sp
		.getAll('hdr_tags')
		.flatMap((value) => value.split(','))
		.map((value) => value.trim())
		.filter(Boolean);

	const query: HdrTvQuery = {
		page: Math.max(1, Number(sp.get('page') ?? '1') || 1),
		page_size: PAGE_SIZE,
		hdr_tags: hdrTags.length ? hdrTags : undefined,
		profile_id: sp.get('profile_id') ? Number(sp.get('profile_id')) : undefined,
		preference_status: (sp.get('preference_status') as ShowStatus) || undefined,
		uniformity: (sp.get('uniformity') as ShowUniformity) || undefined,
		dovi_no_fallback: sp.get('dovi_no_fallback') === 'true' ? true : undefined,
		sort_by: (sp.get('sort_by') as 'title' | 'status' | 'coverage') || 'title',
		sort_dir: (sp.get('sort_dir') as 'asc' | 'desc') || 'asc'
	};

	try {
		const data = await getHdrTv(fetch, query);
		return { data, query, error: null as string | null };
	} catch (e) {
		return {
			data: null,
			query,
			error: e instanceof Error ? e.message : 'Failed to load TV HDR overlay'
		};
	}
};
