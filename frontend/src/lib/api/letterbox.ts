import { ApiError, apiGet, apiSend, type Fetch } from './client';
import { confirmMutation } from '$lib/activity/client';
import type { JobSubmissionResponse } from '$lib/activity/types';
import type {
	BatchReencodeResponse,
	BatchReencodeSettings,
	LetterboxColumn,
	LetterboxDetail,
	LetterboxStatus,
	ReencodeArtifactList,
	ReencodeOptions,
	ReencodePlan,
	LetterboxTvListItem,
	LetterboxTvDetail,
	LetterboxEpisodeDetail,
	LetterboxSummaryResponse,
	TvBatchReencodeResponse,
	TvReplaceReadyResponse
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

export function detectLetterbox(
	fetchFn: Fetch,
	movieId: number,
	options: { thorough?: boolean } = {}
): Promise<JobSubmissionResponse> {
	const suffix = options.thorough ? '?thorough=true' : '';
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		`/letterbox/movies/${movieId}/detect${suffix}`
	);
}

export interface LetterboxPreviewOptions {
	mode?: 'before' | 'after';
	minute?: number;
	exact?: boolean;
}

/** Submit one canonical preview render; image bytes remain job artifacts. */
export function submitMoviePreview(
	fetchFn: Fetch,
	movieId: number,
	options: LetterboxPreviewOptions = {}
): Promise<JobSubmissionResponse> {
	const mode = options.mode ?? 'before';
	const minute = options.minute ?? 5;
	const exact = options.exact ?? false;
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		`/letterbox/movies/${movieId}/preview?mode=${mode}&minute=${minute}&exact=${exact}`
	);
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
export async function reprocessLetterbox(
	fetchFn: Fetch,
	movieId: number
): Promise<JobSubmissionResponse> {
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
export function analyzeAll(fetchFn: Fetch): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/letterbox/detect', {
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

export function batchReencode(
	fetchFn: Fetch,
	movieIds: number[],
	settings: BatchReencodeSettings
): Promise<BatchReencodeResponse> {
	return apiSend<BatchReencodeResponse>(fetchFn, 'POST', '/letterbox/batch/reencode', {
		movie_ids: movieIds,
		settings
	});
}

export function listReencodeArtifacts(
	fetchFn: Fetch,
	q: { movie_id?: number; status?: string; series_id?: number; season_number?: number } = {}
): Promise<ReencodeArtifactList> {
	return apiGet<ReencodeArtifactList>(fetchFn, '/letterbox/reencode-artifacts', {
		movie_id: q.movie_id,
		status: q.status,
		series_id: q.series_id,
		season_number: q.season_number
	});
}

interface ReencodeDecisionPlan {
	job_id: string;
	status: 'planned';
	plan_version: string;
	configuration_version: number;
}

type ReencodeDecisionPath =
	| `/letterbox/reencode-artifacts/${number}/replace-original`
	| `/letterbox/reencode-artifacts/${number}/restore-original`
	| `/letterbox/reencode-artifacts/${number}`;

async function planAndConfirmArtifactDecision(
	fetchFn: Fetch,
	method: 'POST' | 'DELETE',
	path: ReencodeDecisionPath
): Promise<JobSubmissionResponse> {
	const plan = await apiSend<ReencodeDecisionPlan>(
		fetchFn,
		method,
		path,
		method === 'POST' ? {} : undefined
	);
	return confirmMutation(fetchFn, plan.job_id, plan.plan_version, plan.configuration_version);
}

export function replaceOriginal(
	fetchFn: Fetch,
	artifactId: number
): Promise<JobSubmissionResponse> {
	return planAndConfirmArtifactDecision(
		fetchFn,
		'POST',
		`/letterbox/reencode-artifacts/${artifactId}/replace-original`
	);
}

export function restoreOriginal(
	fetchFn: Fetch,
	artifactId: number
): Promise<JobSubmissionResponse> {
	return planAndConfirmArtifactDecision(
		fetchFn,
		'POST',
		`/letterbox/reencode-artifacts/${artifactId}/restore-original`
	);
}

export function deleteArtifact(fetchFn: Fetch, artifactId: number): Promise<JobSubmissionResponse> {
	return planAndConfirmArtifactDecision(
		fetchFn,
		'DELETE',
		`/letterbox/reencode-artifacts/${artifactId}`
	);
}

// ── TV Letterbox Client functions ───────────────────────────────────────────

export function getLetterboxSummary(fetchFn: Fetch): Promise<LetterboxSummaryResponse> {
	return apiGet<LetterboxSummaryResponse>(fetchFn, '/letterbox/summary');
}

export interface TvQuery {
	verdict?: string;
	uniformity?: string;
	has_candidates?: boolean;
	q?: string;
}

export function getLetterboxTv(
	fetchFn: Fetch,
	q?: TvQuery
): Promise<{ total: number; items: LetterboxTvListItem[] }> {
	const params: Record<string, string | number | boolean> = {};
	if (q) {
		if (q.verdict) params.verdict = q.verdict;
		if (q.uniformity) params.uniformity = q.uniformity;
		if (q.has_candidates !== undefined) params.has_candidates = q.has_candidates;
		if (q.q) params.q = q.q;
	}
	return apiGet(fetchFn, '/letterbox/tv', params);
}

export function getLetterboxTvDetail(fetchFn: Fetch, seriesId: number): Promise<LetterboxTvDetail> {
	return apiGet<LetterboxTvDetail>(fetchFn, `/letterbox/tv/${seriesId}`);
}

export function getLetterboxTvEpisodeDetail(
	fetchFn: Fetch,
	seriesId: number,
	episodeId: number
): Promise<LetterboxEpisodeDetail> {
	return apiGet<LetterboxEpisodeDetail>(fetchFn, `/letterbox/tv/${seriesId}/episodes/${episodeId}`);
}

/** Submit one canonical episode preview render; retrieve its artifact from Activity. */
export function submitTvEpisodePreview(
	fetchFn: Fetch,
	seriesId: number,
	episodeId: number,
	options: LetterboxPreviewOptions = {}
): Promise<JobSubmissionResponse> {
	const mode = options.mode ?? 'before';
	const minute = options.minute ?? 5;
	const exact = options.exact ?? false;
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		`/letterbox/tv/${seriesId}/episodes/${episodeId}/preview?mode=${mode}&minute=${minute}&exact=${exact}`
	);
}

export function detectLetterboxTv(
	fetchFn: Fetch,
	seriesId: number,
	body: {
		season_number?: number;
		episode_id?: number;
		exhaustive?: boolean;
		force?: boolean;
		include_open_matte?: boolean;
	} = {}
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/letterbox/tv/${seriesId}/detect`, body);
}

export function detectLetterboxTvLibrary(
	fetchFn: Fetch,
	body: { exhaustive?: boolean; force?: boolean } = {}
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/letterbox/tv/detect', body);
}

export function applyLetterboxTv(
	fetchFn: Fetch,
	seriesId: number,
	body: { season_number?: number; episode_id?: number; confidence_levels?: string[] } = {}
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/letterbox/tv/${seriesId}/apply`, body);
}

export function revertLetterboxTv(
	fetchFn: Fetch,
	seriesId: number,
	body: { season_number?: number; episode_id?: number } = {}
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/letterbox/tv/${seriesId}/revert`, body);
}

export function createTvReencodePlan(
	fetchFn: Fetch,
	seriesId: number,
	episodeId: number,
	opts: ReencodeOptions = {}
): Promise<ReencodePlan> {
	return apiSend<ReencodePlan>(
		fetchFn,
		'POST',
		`/letterbox/tv/${seriesId}/episodes/${episodeId}/reencode-plan`,
		opts
	);
}

export function batchReencodeTv(
	fetchFn: Fetch,
	seriesId: number,
	body: { season_number?: number; confidence_levels?: string[]; settings: BatchReencodeSettings }
): Promise<TvBatchReencodeResponse> {
	return apiSend<TvBatchReencodeResponse>(
		fetchFn,
		'POST',
		`/letterbox/tv/${seriesId}/reencode`,
		body
	);
}

export function replaceReadyTvArtifacts(
	fetchFn: Fetch,
	seriesId: number,
	body: { season_number?: number } = {}
): Promise<TvReplaceReadyResponse> {
	return apiSend<TvReplaceReadyResponse>(
		fetchFn,
		'POST',
		`/letterbox/tv/${seriesId}/reencode-artifacts/replace-ready`,
		body
	);
}

export function resetTvLetterbox(
	fetchFn: Fetch
): Promise<{ states_deleted: number; events_deleted: number; previews_purged: number }> {
	return apiSend(fetchFn, 'POST', '/letterbox/tv/dev/reset-all', {});
}

export function removeLetterboxTvEpisode(
	fetchFn: Fetch,
	seriesId: number,
	episodeId: number
): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/tv/${seriesId}/episodes/${episodeId}/remove`);
}

export function ignoreLetterboxTvEpisode(
	fetchFn: Fetch,
	seriesId: number,
	episodeId: number
): Promise<unknown> {
	return apiSend(fetchFn, 'POST', `/letterbox/tv/${seriesId}/episodes/${episodeId}/ignore`);
}

export function markNotLetterboxedTvEpisode(
	fetchFn: Fetch,
	seriesId: number,
	episodeId: number
): Promise<unknown> {
	return apiSend(
		fetchFn,
		'POST',
		`/letterbox/tv/${seriesId}/episodes/${episodeId}/mark-not-letterboxed`
	);
}
