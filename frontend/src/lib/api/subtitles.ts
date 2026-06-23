import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockSubtitleInventory } from './mock';
import type { SubtitleInventory, SubtitlePlanRequest, SubtitlePlan } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getInventory(fetch: Fetch, mediaFileId: number): Promise<SubtitleInventory> {
	if (useMocks()) return Promise.resolve(mockSubtitleInventory(mediaFileId));
	return apiGet<SubtitleInventory>(fetch, `/media-files/${mediaFileId}/subtitles`);
}

export function scanSubtitles(fetch: Fetch, mediaFileId: number): Promise<SubtitleInventory> {
	if (useMocks()) return Promise.resolve(mockSubtitleInventory(mediaFileId));
	return apiSend<SubtitleInventory>(fetch, 'POST', `/media-files/${mediaFileId}/subtitles/scan`);
}

export function previewTrack(fetch: Fetch, mediaFileId: number, trackId: string): Promise<string> {
	if (useMocks()) return Promise.resolve('1\n00:00:01,000 --> 00:00:04,000\n[Mock Preview] Subtitle content line.');
	return apiGet<string>(fetch, `/media-files/${mediaFileId}/subtitles/${trackId}/preview`);
}

export function inspectMovie(
	fetch: Fetch,
	movieId: number
): Promise<{
	movie_id: number;
	title: string;
	media_file_id: number;
	path_present: boolean;
	inventory: SubtitleInventory;
}> {
	if (useMocks()) {
		return Promise.resolve({
			movie_id: movieId,
			title: 'Mock Movie',
			media_file_id: movieId,
			path_present: true,
			inventory: mockSubtitleInventory(movieId)
		});
	}
	return apiSend<{
		movie_id: number;
		title: string;
		media_file_id: number;
		path_present: boolean;
		inventory: SubtitleInventory;
	}>(fetch, 'POST', `/movies/${movieId}/subtitles/inspect`);
}

export function createPlan(
	fetch: Fetch,
	mediaFileId: number,
	request: SubtitlePlanRequest
): Promise<SubtitlePlan> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-plan-${Date.now()}`,
			status: 'planned',
			operation: request.operation,
			before: {},
			after: {},
			warnings: request.operation === 'subtitle_remove' ? ['This will permanently remux the video file.'] : [],
			capabilities: {
				can_remove: true,
				can_embed_text: true,
				can_embed_bitmap: true,
				can_edit_metadata: true
			},
			storage: {
				estimated_bytes: 120000000,
				available_bytes: 850000000000
			}
		});
	}
	return apiSend<SubtitlePlan>(fetch, 'POST', `/media-files/${mediaFileId}/subtitle-plans`, request);
}

export function extractTrack(
	fetch: Fetch,
	mediaFileId: number,
	trackId: string
): Promise<{ job_id: string; status: 'queued' }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-extract-${Date.now()}`,
			status: 'queued'
		});
	}
	return apiSend<{ job_id: string; status: 'queued' }>(
		fetch,
		'POST',
		`/media-files/${mediaFileId}/subtitles/${trackId}/extract`
	);
}

export function scanLibrarySubtitles(
	fetch: Fetch,
	force: boolean = false
): Promise<{ job_id: string; status: 'queued' }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-scan-all-${Date.now()}`,
			status: 'queued'
		});
	}
	return apiSend<{ job_id: string; status: 'queued' }>(
		fetch,
		'POST',
		`/subtitles/scan-library?force=${force}`
	);
}

