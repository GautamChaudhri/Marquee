import { env } from '$env/dynamic/public';
import { apiGet, type Fetch } from './client';
import { mockMovieDetail, mockMovies } from './mock';
import type { MovieDetail, MovieListItem, MovieQuery, Paginated } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function listMovies(
	fetch: Fetch,
	params: MovieQuery = {}
): Promise<Paginated<MovieListItem>> {
	if (useMocks()) return Promise.resolve(mockMovies(params));
	return apiGet<Paginated<MovieListItem>>(
		fetch,
		'/library/movies',
		params as Record<string, unknown>
	);
}

export function getMovie(fetch: Fetch, id: number): Promise<MovieDetail> {
	if (useMocks()) return Promise.resolve(mockMovieDetail(id));
	return apiGet<MovieDetail>(fetch, `/library/movies/${id}`);
}
