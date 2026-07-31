import { randomUuid } from '$lib/uuid';
import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import type {
	BatchScope,
	CacheSizes,
	MovieRuns,
	OcrLabelCaptureResult,
	OcrLabelClearResult,
	OcrLabelRunState,
	PipelineMetrics,
	PipelineSummary,
	PosterBatchOptions,
	ReviewQueueAutoApproveResult,
	ReviewQueue,
	RunResultsResponse
} from './types';

type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];

/** Submit canonical non-deploying analysis for one movie. */
export function triggerRun(fetchFn: Fetch, movieId: number): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/pipeline/movie/${movieId}/run`);
}

/** Full results for a run (auto-pick, ranked, rejected-by-stage), or — while it
 *  is still executing — `{status:"running", events_url}`. */
export function getRunResults(fetchFn: Fetch, runId: string): Promise<RunResultsResponse> {
	return apiGet<RunResultsResponse>(fetchFn, `/pipeline/runs/${runId}`);
}

/** Submit a canonical ticketless parent over server-frozen movie children. */
export function runBatch(
	fetchFn: Fetch,
	body: { scope: BatchScope; movie_ids?: number[] } & PosterBatchOptions
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/pipeline/batch', body);
}

export function getPipelineSummary(fetchFn: Fetch): Promise<PipelineSummary> {
	return apiGet<PipelineSummary>(fetchFn, '/pipeline/summary');
}

export function rescanPosters(fetchFn: Fetch): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/pipeline/rescan-posters', {});
}

export function backupAllPosters(fetchFn: Fetch): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		'/pipeline/backup-all',
		{},
		{ 'Idempotency-Key': `poster_backup_all:${randomUuid()}` }
	);
}

/** Prune orphaned poster-cache files. Two-phase: a `dry_run` seals a plan whose
 *  `plan_checksum` must come back as `confirmed_plan_checksum` to mutate. */
export function runPosterMaintenance(
	fetchFn: Fetch,
	body: { dry_run?: boolean; confirmed_plan_checksum?: string } = {}
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		'/pipeline/maintenance',
		body,
		{ 'Idempotency-Key': `poster_maintenance:${randomUuid()}` }
	);
}

/** Latest unreviewed run per movie, for the Review tab. */
export function getReviewQueue(
	fetchFn: Fetch,
	params: { page?: number; page_size?: number } = {}
): Promise<ReviewQueue> {
	return apiGet<ReviewQueue>(fetchFn, '/pipeline/review-queue', params);
}

export function approveReviewQueueAutoPicks(
	fetchFn: Fetch,
	body: { deploy?: boolean } = {}
): Promise<ReviewQueueAutoApproveResult> {
	return apiSend<ReviewQueueAutoApproveResult>(
		fetchFn,
		'POST',
		'/pipeline/review-queue/approve-auto',
		body,
		body.deploy === false
			? undefined
			: { 'Idempotency-Key': `poster_deploy:${randomUuid()}` }
	);
}

/** Reset every movie awaiting review, preserving recoverable poster backups. */
export function resetReviewQueuePosters(fetchFn: Fetch): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		'/pipeline/review-queue/reset',
		{},
		{ 'Idempotency-Key': `poster_deploy_reset:${randomUuid()}` }
	);
}

/** Cross-run aggregates for the Metrics tab. */
export function getPipelineMetrics(
	fetchFn: Fetch,
	params: { limit?: number } = {}
): Promise<PipelineMetrics> {
	return apiGet<PipelineMetrics>(fetchFn, '/pipeline/metrics', params);
}

/** On-disk size of each poster-pipeline cache (for the Clear button). */
export function getPipelineCache(fetchFn: Fetch): Promise<CacheSizes> {
	return apiGet<CacheSizes>(fetchFn, '/pipeline/cache');
}

/** Clear downloaded-poster caches (never head/taste/labels). Enqueues a job.
 *
 *  Two-phase like `runPosterMaintenance`: submit with `dry_run` to seal a plan,
 *  then submit again with that plan's `confirmed_plan_checksum` **and the same
 *  include flags** to actually delete. The checksum covers the file list, which
 *  the flags select, so a mismatched pair is rejected by the handler. */
export function clearPipelineCache(
	fetchFn: Fetch,
	body: {
		include_embeddings?: boolean;
		include_archives?: boolean;
		dry_run?: boolean;
		confirmed_plan_checksum?: string;
	} = {}
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		'/pipeline/cache/clear',
		body,
		{ 'Idempotency-Key': `pipeline_cache_clear:${randomUuid()}` }
	);
}

/** Run history for one movie, newest first. */
export function listMovieRuns(fetchFn: Fetch, movieId: number): Promise<MovieRuns> {
	return apiGet<MovieRuns>(fetchFn, `/movies/${movieId}/runs`);
}

export function markOcrFalseRejection(
	fetchFn: Fetch,
	body: { run_id: string; orig_filename: string }
): Promise<OcrLabelCaptureResult> {
	return apiSend<OcrLabelCaptureResult>(fetchFn, 'POST', '/dev/ocr-labels/false-rejection', body);
}

export function markOcrFalseAcceptance(
	fetchFn: Fetch,
	body: { run_id: string; orig_filename: string }
): Promise<OcrLabelCaptureResult> {
	return apiSend<OcrLabelCaptureResult>(fetchFn, 'POST', '/dev/ocr-labels/false-acceptance', body);
}

export function clearOcrLabels(fetchFn: Fetch): Promise<OcrLabelClearResult> {
	return apiSend<OcrLabelClearResult>(fetchFn, 'POST', '/dev/ocr-labels/clear');
}

export function getOcrLabelState(fetchFn: Fetch, runId: string): Promise<OcrLabelRunState> {
	return apiGet<OcrLabelRunState>(fetchFn, `/dev/ocr-labels/run/${runId}`);
}
