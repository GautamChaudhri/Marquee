import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import type { MediaJob } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getMediaJob(fetch: Fetch, jobId: string): Promise<MediaJob> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: jobId,
			status: 'running',
			operation: 'subtitle_remove',
			label: 'Subtitle Removal',
			media_file_id: 'mf-1',
			created_at: new Date().toISOString(),
			updated_at: new Date().toISOString(),
			started_at: new Date().toISOString(),
			completed_at: null,
			result: null,
			error: null,
			progress: {
				stage: 'remux.start',
				percent: 45,
				message: 'Remuxing container to remove track'
			},
			events_url: `/media-jobs/${jobId}/events`,
			backup_id: 'backup-1',
			plan: null
		});
	}
	return apiGet<MediaJob>(fetch, `/media-jobs/${jobId}`);
}

export function listMediaJobs(
	fetch: Fetch,
	params: { status?: string; operation?: string } = {}
): Promise<{ jobs: MediaJob[] }> {
	if (useMocks()) {
		return Promise.resolve({
			jobs: [
				{
					job_id: 'job-1',
					status: 'completed',
					operation: 'subtitle_remove',
					label: 'Subtitle Removal',
					media_file_id: 'mf-1',
					created_at: new Date().toISOString(),
					updated_at: new Date().toISOString(),
					started_at: new Date().toISOString(),
					completed_at: new Date().toISOString(),
					result: { message: 'Successfully removed 1 track' },
					error: null,
					progress: { stage: 'done.complete', percent: 100, message: 'Done' },
					events_url: '/media-jobs/job-1/events',
					backup_id: 'backup-1',
					plan: null
				}
			]
		});
	}
	return apiGet<{ jobs: MediaJob[] }>(fetch, '/media-jobs', params);
}

export function confirmJob(
	fetch: Fetch,
	jobId: string
): Promise<{ job_id: string; status: 'queued' }> {
	if (useMocks()) return Promise.resolve({ job_id: jobId, status: 'queued' });
	return apiSend<{ job_id: string; status: 'queued' }>(
		fetch,
		'POST',
		`/media-jobs/${jobId}/confirm`
	);
}

export function cancelJob(
	fetch: Fetch,
	jobId: string
): Promise<{ job_id: string; cancel_requested: boolean }> {
	if (useMocks()) return Promise.resolve({ job_id: jobId, cancel_requested: true });
	return apiSend<{ job_id: string; cancel_requested: boolean }>(
		fetch,
		'POST',
		`/media-jobs/${jobId}/cancel`
	);
}

export function restoreJob(fetch: Fetch, jobId: string): Promise<Record<string, unknown>> {
	if (useMocks()) return Promise.resolve({ success: true, message: 'Restored from backup' });
	return apiSend<Record<string, unknown>>(fetch, 'POST', `/media-jobs/${jobId}/restore`);
}

export function deleteBackup(fetch: Fetch, jobId: string): Promise<Record<string, unknown>> {
	if (useMocks()) return Promise.resolve({ success: true, message: 'Deleted backup file' });
	return apiSend<Record<string, unknown>>(fetch, 'DELETE', `/media-jobs/${jobId}/backup`);
}
