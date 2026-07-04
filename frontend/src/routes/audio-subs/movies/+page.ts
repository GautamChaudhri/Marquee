import type { PageLoad } from './$types';
import { listMovies } from '$lib/api/library';
import type { Paginated, MovieListItem } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, url }) => {
	const status = url.searchParams.get('status');
	try {
		const movies = await listMovies(fetch, { page_size: 200 });
		return { movies, status, error: null as string | null };
	} catch (e) {
		return {
			movies: null as Paginated<MovieListItem> | null,
			status,
			error: e instanceof Error ? e.message : 'Failed to load library movies list'
		};
	}
};
