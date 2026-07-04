import type { PageLoad } from './$types';
import { getAudioSubsTvDetail, getAudioSubsSummary } from '$lib/api/subtitles';
import { getSettings } from '$lib/api/system';

export const load: PageLoad = async ({ fetch, params }) => {
	const id = Number(params.id);
	if (!id) return { detail: null, settings: null, summary: null, error: 'Invalid TV series ID' };

	try {
		const [detail, settings, summary] = await Promise.all([
			getAudioSubsTvDetail(fetch, id),
			getSettings(fetch),
			getAudioSubsSummary(fetch)
		]);
		return { detail, settings, summary, error: null as string | null };
	} catch (e) {
		return {
			detail: null,
			settings: null,
			summary: null,
			error: e instanceof Error ? e.message : 'Failed to load TV series details'
		};
	}
};
