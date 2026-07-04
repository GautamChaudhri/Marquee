import type { PageLoad } from './$types';
import { getAudioSubsSummary } from '$lib/api/subtitles';
import { getSettings } from '$lib/api/system';

export const load: PageLoad = async ({ fetch }) => {
	try {
		const [summary, settings] = await Promise.all([getAudioSubsSummary(fetch), getSettings(fetch)]);
		return { summary, settings, error: null as string | null };
	} catch (e) {
		return {
			summary: null,
			settings: null,
			error: e instanceof Error ? e.message : 'Failed to load audio-subs dashboard summary'
		};
	}
};
