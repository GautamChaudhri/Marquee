import type { PageLoad } from './$types';
import { getHdrTvDetail } from '$lib/api/hdr';

export const load: PageLoad = async ({ fetch, params }) => {
	const seriesId = Number(params.id);
	try {
		const detail = await getHdrTvDetail(fetch, seriesId);
		return { detail, error: null as string | null };
	} catch (e) {
		return {
			detail: null,
			error: e instanceof Error ? e.message : 'Failed to load show HDR detail'
		};
	}
};
