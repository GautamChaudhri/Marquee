import type { PageLoad } from './$types';
import { listMovies } from '$lib/api/library';
import { getSettings } from '$lib/api/system';
import type { Paginated, MovieListItem } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	try {
		const [movies, settings] = await Promise.all([
			listMovies(fetch, { page_size: 200 }),
			getSettings(fetch)
		]);
		return { movies, settings, error: null as string | null };
	} catch (e) {
		return {
			movies: null as Paginated<MovieListItem> | null,
			settings: null,
			error: e instanceof Error ? e.message : 'Failed to load library movies list'
		};
	}
};
