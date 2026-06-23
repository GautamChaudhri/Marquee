import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockGenerators } from './mock';
import type { SubtitleGenerator, GenerationRequest } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getGenerators(fetch: Fetch): Promise<{ generators: SubtitleGenerator[] }> {
	if (useMocks()) return Promise.resolve(mockGenerators());
	return apiGet<{ generators: SubtitleGenerator[] }>(fetch, '/subtitle-generators');
}

export function submitGeneration(
	fetch: Fetch,
	mediaFileId: number,
	request: GenerationRequest
): Promise<{ job_id: string; events_url: string }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-gen-${Date.now()}`,
			events_url: `/api/media-jobs/job-mock-gen-${Date.now()}/events`
		});
	}
	return apiSend<{ job_id: string; events_url: string }>(
		fetch,
		'POST',
		`/media-files/${mediaFileId}/subtitle-generations`,
		request
	);
}

export function submitMovieGeneration(
	fetch: Fetch,
	movieId: number,
	request: GenerationRequest
): Promise<{ job_id: string; events_url: string }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-gen-${Date.now()}`,
			events_url: `/api/media-jobs/job-mock-gen-${Date.now()}/events`
		});
	}
	return apiSend<{ job_id: string; events_url: string }>(
		fetch,
		'POST',
		`/movies/${movieId}/subtitle-generations`,
		request
	);
}
