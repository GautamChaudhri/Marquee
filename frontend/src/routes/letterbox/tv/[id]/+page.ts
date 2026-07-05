import type { PageLoad } from './$types';
import { getLetterboxTvDetail } from '$lib/api/letterbox';

export const load: PageLoad = async ({ fetch, params }) => {
	const seriesId = Number(params.id);
	const detail = await getLetterboxTvDetail(fetch, seriesId);
	return {
		detail
	};
};
