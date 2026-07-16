import { env } from '$env/dynamic/public';
import { confirmMutation } from '$lib/activity/client';
import type { JobSubmissionResponse, PlannedJobSubmissionResponse } from '$lib/activity/types';
import { apiGet, apiSend, type ApiPathFor, type Fetch } from './client';
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

export function analyzeMovieDovi(fetch: Fetch, movieId: number): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetch, 'POST', `/hdr/${movieId}/analyze`);
}

export function convertMovieDovi(
	fetch: Fetch,
	movieId: number,
	kind: 'p5_to_p81' | 'p7_strip_el'
): Promise<PlannedJobSubmissionResponse> {
	return apiSend<PlannedJobSubmissionResponse>(fetch, 'POST', `/hdr/${movieId}/convert`, {
		kind
	}).then(async (planned) => {
		await confirmMutation(
			fetch,
			planned.job_id,
			planned.plan_version,
			planned.configuration_version
		);
		return planned;
	});
}

async function confirmDoviDecision(
	fetch: Fetch,
	path: ApiPathFor<'post'>
): Promise<PlannedJobSubmissionResponse> {
	const planned = await apiSend<PlannedJobSubmissionResponse>(fetch, 'POST', path);
	await confirmMutation(fetch, planned.job_id, planned.plan_version, planned.configuration_version);
	return planned;
}

export function publishDoviCandidate(fetch: Fetch, movieId: number, artifactId: number) {
	return confirmDoviDecision(fetch, `/hdr/${movieId}/conversion-candidates/${artifactId}/publish`);
}

export function restoreDoviCandidate(fetch: Fetch, movieId: number, artifactId: number) {
	return confirmDoviDecision(fetch, `/hdr/${movieId}/conversion-candidates/${artifactId}/restore`);
}

export function discardDoviCandidate(fetch: Fetch, movieId: number, artifactId: number) {
	return confirmDoviDecision(fetch, `/hdr/${movieId}/conversion-candidates/${artifactId}/discard`);
}

export function analyzeDoviBatch(
	fetch: Fetch,
	movieIds?: number[]
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(
		fetch,
		'POST',
		'/hdr/analyze',
		movieIds ? { movie_ids: movieIds } : {}
	);
}
