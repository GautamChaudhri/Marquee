import { apiGet, apiSend, type Fetch } from './client';
import type {
	BatchScope,
	CacheSizes,
	JobSummary,
	MovieRuns,
	OcrLabelCaptureResult,
	OcrLabelClearResult,
	OcrLabelRunState,
	PipelineMetrics,
	PipelineRunRef,
	ReviewQueue,
	RunResultsResponse
} from './types';

/** Start a single-movie pipeline run. 202 + run_id, or 409 if one is active. */
export function triggerRun(fetchFn: Fetch, movieId: number): Promise<PipelineRunRef> {
	return apiSend<PipelineRunRef>(fetchFn, 'POST', `/pipeline/movie/${movieId}/run`);
}

/** Full results for a run (auto-pick, ranked, rejected-by-stage), or — while it
 *  is still executing — `{status:"running", events_url}`. */
export function getRunResults(fetchFn: Fetch, runId: string): Promise<RunResultsResponse> {
	return apiGet<RunResultsResponse>(fetchFn, `/pipeline/runs/${runId}`);
}

/** Enqueue one stage-batched run over many movies (OCR/DINO load once). */
export function runBatch(
	fetchFn: Fetch,
	body: { scope: BatchScope; movie_ids?: number[] }
): Promise<JobSummary> {
	return apiSend<JobSummary>(fetchFn, 'POST', '/pipeline/batch', body);
}

/** Latest unreviewed run per movie, for the Review tab. */
export function getReviewQueue(
	fetchFn: Fetch,
	params: { page?: number; page_size?: number } = {}
): Promise<ReviewQueue> {
	return apiGet<ReviewQueue>(fetchFn, '/pipeline/review-queue', params);
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

/** Clear downloaded-poster caches (never head/taste/labels). Enqueues a job. */
export function clearPipelineCache(
	fetchFn: Fetch,
	body: { include_embeddings?: boolean; include_archives?: boolean } = {}
): Promise<JobSummary> {
	return apiSend<JobSummary>(fetchFn, 'POST', '/pipeline/cache/clear', body);
}

/** Run history for one movie, newest first. */
export function listMovieRuns(fetchFn: Fetch, movieId: number): Promise<MovieRuns> {
	return apiGet<MovieRuns>(fetchFn, `/movies/${movieId}/runs`);
}

export function markOcrFalsePositive(
	fetchFn: Fetch,
	body: { run_id: string; orig_filename: string }
): Promise<OcrLabelCaptureResult> {
	return apiSend<OcrLabelCaptureResult>(fetchFn, 'POST', '/dev/ocr-labels/false-positive', body);
}

export function markOcrFalseNegative(
	fetchFn: Fetch,
	body: { run_id: string; orig_filename: string }
): Promise<OcrLabelCaptureResult> {
	return apiSend<OcrLabelCaptureResult>(fetchFn, 'POST', '/dev/ocr-labels/false-negative', body);
}

export function clearOcrLabels(fetchFn: Fetch): Promise<OcrLabelClearResult> {
	return apiSend<OcrLabelClearResult>(fetchFn, 'POST', '/dev/ocr-labels/clear');
}

export function getOcrLabelState(fetchFn: Fetch, runId: string): Promise<OcrLabelRunState> {
	return apiGet<OcrLabelRunState>(fetchFn, `/dev/ocr-labels/run/${runId}`);
}
