import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import type {
	PipelineMetrics,
	SeriesArtworkEventsResponse,
	SeriesRunsResponse,
	TvAutoApproveResult,
	TvPipelineSummary,
	TvReviewQueue,
	TvRunQueue
} from './types';

type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];

export function getTvSummary(fetchFn: Fetch): Promise<TvPipelineSummary> {
	return apiGet<TvPipelineSummary>(fetchFn, '/pipeline/tv/summary');
}

export function getTvRunQueue(fetchFn: Fetch): Promise<TvRunQueue> {
	return apiGet<TvRunQueue>(fetchFn, '/pipeline/tv/run-queue');
}

export function runTvBatch(
	fetchFn: Fetch,
	body: { scope: 'missing' | 'all' | 'selected'; series_ids?: number[] }
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/pipeline/tv/batch', body);
}

export function runSeries(
	fetchFn: Fetch,
	seriesId: number,
	body: { include: 'all_missing' | 'show' | 'seasons'; season_ids?: number[] }
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		`/pipeline/tv/series/${seriesId}/run`,
		body
	);
}

export function getTvReviewQueue(
	fetchFn: Fetch,
	params: { page?: number; page_size?: number } = {}
): Promise<TvReviewQueue> {
	return apiGet<TvReviewQueue>(fetchFn, '/pipeline/tv/review-queue', params);
}

export function approveTvAuto(
	fetchFn: Fetch,
	body: { deploy?: boolean; series_id?: number } = {}
): Promise<TvAutoApproveResult> {
	return apiSend<TvAutoApproveResult>(
		fetchFn,
		'POST',
		'/pipeline/tv/review-queue/approve-auto',
		body,
		body.deploy === false
			? undefined
			: { 'Idempotency-Key': `poster_deploy:${crypto.randomUUID()}` }
	);
}

export function resetTvReview(fetchFn: Fetch): Promise<{ reset: number }> {
	return apiSend(fetchFn, 'POST', '/pipeline/tv/review/reset', {});
}

export function useShowPoster(fetchFn: Fetch, seasonId: number): Promise<JobSubmissionResponse> {
	return apiSend(
		fetchFn,
		'POST',
		`/pipeline/tv/seasons/${seasonId}/use-show-poster`,
		{},
		{
			'Idempotency-Key': `poster_deploy:${crypto.randomUUID()}`
		}
	);
}

export function getTvMetrics(
	fetchFn: Fetch,
	params: { limit?: number } = {}
): Promise<PipelineMetrics> {
	return apiGet<PipelineMetrics>(fetchFn, '/pipeline/tv/metrics', params);
}

export function getSeriesRuns(fetchFn: Fetch, seriesId: number): Promise<SeriesRunsResponse> {
	return apiGet<SeriesRunsResponse>(fetchFn, `/series/${seriesId}/runs`);
}

export function getSeriesArtworkEvents(
	fetchFn: Fetch,
	seriesId: number
): Promise<SeriesArtworkEventsResponse> {
	return apiGet<SeriesArtworkEventsResponse>(fetchFn, `/series/${seriesId}/artwork-events`);
}
