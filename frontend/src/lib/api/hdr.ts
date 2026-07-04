import { apiGet, apiSend, type Fetch } from './client';
import type {
	HdrPreferenceChoice,
	HdrSummary,
	HdrTvDetail,
	HdrTvListResponse,
	HdrTvQuery
} from './types';

export function getHdrSummary(fetch: Fetch): Promise<HdrSummary> {
	return apiGet<HdrSummary>(fetch, '/hdr/summary');
}

export function getHdrTv(fetch: Fetch, params: HdrTvQuery = {}): Promise<HdrTvListResponse> {
	const query = {
		...params,
		hdr_tags: params.hdr_tags?.join(',')
	};
	return apiGet<HdrTvListResponse>(fetch, '/hdr/tv', query as unknown as Record<string, unknown>);
}

export function getHdrTvDetail(fetch: Fetch, seriesId: number): Promise<HdrTvDetail> {
	return apiGet<HdrTvDetail>(fetch, `/hdr/tv/${seriesId}`);
}

export function putSonarrPreferences(
	fetch: Fetch,
	profiles: Array<{
		profile_id: number;
		meet_target: HdrPreferenceChoice | null;
		exceed_target: HdrPreferenceChoice | null;
		excluded_targets: HdrPreferenceChoice[];
	}>
) {
	return apiSend<{ applied_profile_ids: number[] }>(fetch, 'PUT', '/hdr/tv/preferences', {
		profiles
	});
}

export function analyzeSeriesDovi(
	fetch: Fetch,
	seriesId: number,
	seasonNumber?: number | null
): Promise<{ job_id: string; total: number; events_url: string }> {
	return apiSend(fetch, 'POST', `/hdr/tv/${seriesId}/analyze`, {
		season_number: seasonNumber ?? null
	});
}

export function analyzeTvDovi(
	fetch: Fetch,
	seriesIds?: number[]
): Promise<{ job_id: string; total: number; events_url: string }> {
	return apiSend(fetch, 'POST', '/hdr/tv/analyze', seriesIds ? { series_ids: seriesIds } : {});
}
