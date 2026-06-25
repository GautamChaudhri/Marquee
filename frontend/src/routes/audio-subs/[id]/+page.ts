import { getMovie } from '$lib/api/library';
import { inspectMovie } from '$lib/api/subtitles';
import { getSettings } from '$lib/api/system';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params }) => {
	const id = Number(params.id);
	if (!id) return { movie: null, inspect: null, settings: null, error: 'Invalid movie ID' };
	try {
		const [movie, inspect, settings] = await Promise.all([
			getMovie(fetch, id),
			inspectMovie(fetch, id),
			getSettings(fetch)
		]);
		return { movie, inspect, settings, error: null as string | null };
	} catch (e) {
		return {
			movie: null,
			inspect: null,
			settings: null,
			error: e instanceof Error ? e.message : 'Failed to load movie subtitle data'
		};
	}
};
