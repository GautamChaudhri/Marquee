import { ApiError, apiGet, apiSend, type Fetch } from './client';
import type {
	LetterboxColumn,
	LetterboxDetail,
	LetterboxJobRef,
	LetterboxStatus
} from './types';

// ── Single-movie state + actions (also used by the film detail hub) ──────────

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
	// Empty body — backend applies the stored recommended_crop values.
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/apply`, {});
}

export function ignoreLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/ignore`);
}

export function removeLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/remove`);
}

/** "Confirm & finish" — marks a tagged movie reviewed so it moves to Processed. */
export function confirmLetterbox(fetchFn: Fetch, movieId: number): Promise<LetterboxDetail> {
	return apiSend<LetterboxDetail>(fetchFn, 'POST', `/letterbox/movies/${movieId}/confirm`);
}

/** Reprocess: strip the tag, then re-run frame analysis. Lands back in Detected. */
export async function reprocessLetterbox(
	fetchFn: Fetch,
	movieId: number
): Promise<LetterboxDetail> {
	await removeLetterbox(fetchFn, movieId);
	return detectLetterbox(fetchFn, movieId);
}

// ── Board-level ──────────────────────────────────────────────────────────────

export function getLetterboxStatus(fetchFn: Fetch): Promise<LetterboxStatus> {
	return apiGet<LetterboxStatus>(fetchFn, '/letterbox/status');
}

export interface ColumnQuery {
	status: string; // comma-separated
	reviewed?: boolean;
	sort?: 'confidence' | 'title' | 'crop' | 'recent';
	desc?: boolean; // confidence sort direction: true = high→low
	page_size?: number;
}

export function listColumn(fetchFn: Fetch, q: ColumnQuery): Promise<LetterboxColumn> {
	return apiGet<LetterboxColumn>(fetchFn, '/letterbox/candidates', {
		status: q.status,
		reviewed: q.reviewed,
		sort: q.sort ?? 'confidence',
		desc: q.desc ?? true,
		page: 1,
		page_size: q.page_size ?? 25
	});
}

/** Resolution prefilter scan — populates the Candidates column. */
export function scanLibrary(fetchFn: Fetch): Promise<unknown> {
	return apiGet(fetchFn, '/letterbox/movies/find-candidates');
}

/** Start a batch frame-analysis job over all candidates. Returns the SSE ref. */
export function analyzeAll(fetchFn: Fetch): Promise<LetterboxJobRef> {
	return apiSend<LetterboxJobRef>(fetchFn, 'POST', '/letterbox/detect', {
		all_candidates: true
	});
}

export function healDrift(fetchFn: Fetch): Promise<{ checked: number; reapplied: number }> {
	return apiSend(fetchFn, 'POST', '/letterbox/heal');
}

/** Apply recommended crop tags to many movies at once. */
export function applyBatch(fetchFn: Fetch, movieIds: number[]): Promise<unknown> {
	return apiSend(fetchFn, 'POST', '/letterbox/apply', { movie_ids: movieIds });
}
