import type { PageLoad } from './$types';
import { getAudioSubsTv } from '$lib/api/subtitles';

export const load: PageLoad = async ({ fetch, url }) => {
	const status = url.searchParams.get('status');
	const uniformity = url.searchParams.get('uniformity');
	const missing_language = url.searchParams.get('missing_language');
	const q = url.searchParams.get('q');
	const sort_by =
		(url.searchParams.get('sort_by') as 'title' | 'status' | 'coverage' | null) || 'title';

	try {
		const result = await getAudioSubsTv(fetch, {
			status,
			uniformity,
			missing_language,
			q,
			sort_by
		});
		return { result, error: null as string | null };
	} catch (e) {
		return {
			result: null,
			error: e instanceof Error ? e.message : 'Failed to load TV subtitles library'
		};
	}
};
