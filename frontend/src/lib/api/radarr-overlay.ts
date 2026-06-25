import { env } from '$env/dynamic/public';
import type { JobSnapshot } from './jobs';
import { apiGet, apiSend, type Fetch } from './client';
import { mockRadarrOverlay } from './mock';
import type {
	HdrMovieDetail,
	HdrPreferenceChoice,
	RadarrOverlayQuery,
	RadarrOverlayResponse
} from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getRadarrOverlay(
	fetch: Fetch,
	params: RadarrOverlayQuery = {}
): Promise<RadarrOverlayResponse> {
	if (useMocks()) return Promise.resolve(mockRadarrOverlay(params));
	const query = {
		...params,
		hdr_tags: params.hdr_tags?.join(',')
	};
	return apiGet<RadarrOverlayResponse>(fetch, '/hdr', query as unknown as Record<string, unknown>);
}

export function putRadarrOverlayPreferences(
	fetch: Fetch,
	profiles: Array<{
		profile_id: number;
		meet_target: HdrPreferenceChoice | null;
		exceed_target: HdrPreferenceChoice | null;
		excluded_targets: HdrPreferenceChoice[];
	}>
) {
	return apiSend<{ applied_profile_ids: number[] }>(fetch, 'PUT', '/hdr/preferences', {
		profiles
	});
}

export function getHdrDetail(fetch: Fetch, movieId: number): Promise<HdrMovieDetail> {
	return apiGet<HdrMovieDetail>(fetch, `/hdr/${movieId}`);
}

export function analyzeMovieDovi(fetch: Fetch, movieId: number): Promise<JobSnapshot> {
	return apiSend<JobSnapshot>(fetch, 'POST', `/hdr/${movieId}/analyze`);
}

export function analyzeDoviBatch(
	fetch: Fetch,
	movieIds?: number[]
): Promise<{ job_id: string; total: number; events_url: string }> {
	return apiSend<{ job_id: string; total: number; events_url: string }>(
		fetch,
		'POST',
		'/hdr/analyze',
		movieIds ? { movie_ids: movieIds } : {}
	);
}
