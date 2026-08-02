import type { PageLoad } from './$types';
import { listMovies } from '$lib/api/library';
import type { MovieQuery, PosterStatus } from '$lib/api/types';

const PAGE_SIZE = 60;

function filmLibraryHeader(total?: number): App.LibraryHeader {
	return {
		title: 'Film Library',
		...(total === undefined ? {} : { countLabel: `${total} ${total === 1 ? 'movie' : 'movies'}` })
	};
}

export const load: PageLoad = async ({ fetch, url }) => {
	const sp = url.searchParams;
	const query: MovieQuery = {
		page: Math.max(1, Number(sp.get('page') ?? '1') || 1),
		page_size: PAGE_SIZE,
		q: sp.get('q') || undefined,
		poster_status: (sp.get('poster_status') as PosterStatus) || undefined,
		sort: (sp.get('sort') as 'title' | 'year') || undefined
	};
	try {
		const data = await listMovies(fetch, query);
		return {
			data,
			query,
			error: null as string | null,
			libraryHeader: filmLibraryHeader(data.total)
		};
	} catch (e) {
		return {
			data: null,
			query,
			error: e instanceof Error ? e.message : 'Failed to load movies',
			libraryHeader: filmLibraryHeader()
		};
	}
};
