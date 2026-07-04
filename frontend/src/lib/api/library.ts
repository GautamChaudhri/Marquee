import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockMovieDetail, mockMovies } from './mock';
import type {
	MovieDetail,
	MovieListItem,
	MovieQuery,
	Paginated,
	SeriesDetail,
	SeriesListItem
} from './types';

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

export function deleteMoviePoster(
	fetch: Fetch,
	id: number
): Promise<{ ok: boolean; deleted: boolean; error: string | null }> {
	if (useMocks()) return Promise.resolve({ ok: true, deleted: true, error: null });
	return apiSend(fetch, 'DELETE', `/library/movies/${id}/poster`);
}

export function listSeries(
	fetch: Fetch,
	params: { page?: number; page_size?: number } = {}
): Promise<Paginated<SeriesListItem>> {
	return apiGet<Paginated<SeriesListItem>>(fetch, '/library/series', params);
}

export function getSeries(fetch: Fetch, id: number): Promise<SeriesDetail> {
	return apiGet<SeriesDetail>(fetch, `/library/series/${id}`);
}

export function getSeriesPosterUrl(id: number): string {
	return `/api/library/series/${id}/poster`;
}

export function getSeasonPosterUrl(id: number): string {
	return `/api/library/seasons/${id}/poster`;
}

export function deleteSeriesPoster(
	fetch: Fetch,
	id: number
): Promise<{ ok: boolean; deleted: boolean; error: string | null }> {
	return apiSend(fetch, 'DELETE', `/library/series/${id}/poster`);
}

export function deleteSeasonPoster(
	fetch: Fetch,
	id: number
): Promise<{ ok: boolean; deleted: boolean; error: string | null }> {
	return apiSend(fetch, 'DELETE', `/library/seasons/${id}/poster`);
}
