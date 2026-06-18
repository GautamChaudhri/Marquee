import { getMovie } from '$lib/api/library';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params, url }) => {
	const id = Number(params.id);
	if (!id) return { movie: null, tab: 'poster', error: 'Invalid movie id' };
	try {
		const movie = await getMovie(fetch, id);
		const tab = url.searchParams.get('tab') ?? 'poster';
		return { movie, tab, error: null };
	} catch (e) {
		return {
			movie: null,
			tab: 'poster',
			error: e instanceof Error ? e.message : 'Failed to load movie'
		};
	}
};
