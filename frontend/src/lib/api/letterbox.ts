import { ApiError, apiGet, apiSend, type Fetch } from './client';
import type {
	LetterboxColumn,
	LetterboxDetail,
	LetterboxJobRef,
	LetterboxStatus,
	MediaJobSnapshot,
	ReencodeArtifact,
	ReencodeArtifactList,
	ReencodeOptions,
	ReencodePlan
} from './types';
import type { JobSnapshot } from './jobs';

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

export function detectLetterbox(fetchFn: Fetch, movieId: number): Promise<JobSnapshot> {
	return apiSend<JobSnapshot>(fetchFn, 'POST', `/letterbox/movies/${movieId}/detect`);
}

export function applyLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	// Empty body — backend applies the stored recommended_crop values.
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/apply`, {});
}

export function ignoreLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/ignore`);
}

export function markNotLetterboxed(fetchFn: Fetch, movieId: number): Promise<LetterboxDetail> {
	return apiSend<LetterboxDetail>(
		fetchFn,
		'POST',
		`/letterbox/movies/${movieId}/mark-not-letterboxed`
	);
}

export function removeLetterbox(fetchFn: Fetch, movieId: number): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/movies/${movieId}/remove`);
}

/** "Confirm & finish" — marks a tagged movie reviewed so it moves to Processed. */
export function confirmLetterbox(fetchFn: Fetch, movieId: number): Promise<LetterboxDetail> {
	return apiSend<LetterboxDetail>(fetchFn, 'POST', `/letterbox/movies/${movieId}/confirm`);
}

/** Reprocess: strip the tag, then re-run frame analysis. Lands back in Staging. */
export async function reprocessLetterbox(fetchFn: Fetch, movieId: number): Promise<JobSnapshot> {
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

/** Legacy/debug resolution prefilter refresh; normal prefiltering happens during sync. */
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

// ── Permanent re-encode (media-job workflow) ─────────────────────────────────

/** Plan a permanent cropped re-encode. Returns the resolved encoder/quality/
 *  HDR-DV plan + a planned job id. No media is written until confirmed. */
export function createReencodePlan(
	fetchFn: Fetch,
	movieId: number,
	opts: ReencodeOptions = {}
): Promise<ReencodePlan> {
	return apiSend<ReencodePlan>(fetchFn, 'POST', `/letterbox/movies/${movieId}/reencode-plan`, opts);
}

/** Confirm a planned media job → queues it for the durable worker. */
export function confirmJob(
	fetchFn: Fetch,
	jobId: string
): Promise<{ job_id: string; status: string }> {
	return apiSend(fetchFn, 'POST', `/media-jobs/${jobId}/confirm`, {});
}

/** Fetch a media job's current snapshot, including its recorded error (if failed). */
export function getMediaJob(fetchFn: Fetch, jobId: string): Promise<MediaJobSnapshot> {
	return apiGet<MediaJobSnapshot>(fetchFn, `/media-jobs/${jobId}`);
}

/** Cancel a media job. A planned/queued job is dropped; a running one is asked to stop. */
export function cancelJob(
	fetchFn: Fetch,
	jobId: string
): Promise<{ job_id: string; cancel_requested: boolean }> {
	return apiSend(fetchFn, 'POST', `/media-jobs/${jobId}/cancel`, {});
}

export function listReencodeArtifacts(
	fetchFn: Fetch,
	q: { movie_id?: number; status?: string } = {}
): Promise<ReencodeArtifactList> {
	return apiGet<ReencodeArtifactList>(fetchFn, '/letterbox/reencode-artifacts', {
		movie_id: q.movie_id,
		status: q.status
	});
}

export function replaceOriginal(fetchFn: Fetch, artifactId: number): Promise<ReencodeArtifact> {
	return apiSend<ReencodeArtifact>(
		fetchFn,
		'POST',
		`/letterbox/reencode-artifacts/${artifactId}/replace-original`,
		{}
	);
}

export function restoreOriginal(
	fetchFn: Fetch,
	artifactId: number,
	keepCandidate = false
): Promise<ReencodeArtifact> {
	return apiSend<ReencodeArtifact>(
		fetchFn,
		'POST',
		`/letterbox/reencode-artifacts/${artifactId}/restore-original`,
		{ keep_candidate: keepCandidate }
	);
}

export function deleteArtifact(fetchFn: Fetch, artifactId: number): Promise<unknown> {
	return apiSend(fetchFn, 'DELETE', `/letterbox/reencode-artifacts/${artifactId}`);
}

export type { MediaJobSnapshot } from './types';
