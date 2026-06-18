import { ApiError, apiGet, apiSend, type Fetch } from './client';
import type { LetterboxDetail } from './types';

export async function getLetterboxState(
	fetchFn: Fetch,
	movieId: number
): Promise<LetterboxDetail | null> {
	try {
		return await apiGet<LetterboxDetail>(fetchFn, `/letterbox/movies/${movieId}`);
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) return null;
		throw e;
	}
}

export function detectLetterbox(fetchFn: Fetch, movieId: number): Promise<LetterboxDetail> {
	return apiSend<LetterboxDetail>(fetchFn, 'POST', `/letterbox/movies/${movieId}/detect`);
}

export function applyLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	// POST with empty body — backend uses recommended_crop values from stored state
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/apply`, {});
}

export function ignoreLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/ignore`);
}

export function removeLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/remove`);
}
