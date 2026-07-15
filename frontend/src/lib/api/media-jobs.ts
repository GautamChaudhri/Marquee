import { env } from '$env/dynamic/public';
import { ApiError, apiGet, apiSend, type Fetch } from './client';
import { cancelJob as cancelCanonicalJob } from './jobs';
import type { components } from './generated/openapi';
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
			events_url: `/jobs/${jobId}/snapshot`,
			backup_id: 'backup-1',
			plan: null
		});
	}
	return apiGet<components['schemas']['JobSnapshotResponse']>(
		fetch,
		`/jobs/${jobId}/snapshot`
	).then((snapshot) => ({
		job_id: snapshot.job_id,
		status: snapshot.outcome ?? snapshot.phase,
		operation: snapshot.type,
		label: snapshot.label,
		media_file_id: null,
		created_at: snapshot.created_at ?? '',
		updated_at: snapshot.updated_at ?? snapshot.created_at ?? '',
		started_at: snapshot.started_at,
		completed_at: snapshot.terminal_at,
		result: null,
		error: null,
		progress: snapshot.progress
			? {
					stage: snapshot.progress.stage_key ?? '',
					percent: snapshot.progress.overall?.percent ?? 0,
					message: snapshot.progress.headline ?? snapshot.progress.stage_label ?? ''
				}
			: null,
		events_url: snapshot.links.snapshot,
		backup_id: null,
		plan: null
	}));
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
					events_url: '/jobs/job-1/snapshot',
					backup_id: 'backup-1',
					plan: null
				}
			]
		});
	}
	const queueStatuses = new Set(['planned', 'queued', 'running', 'stopping']);
	const view = params.status && !queueStatuses.has(params.status) ? 'history' : 'queue';
	return apiGet<components['schemas']['JobListResponse']>(fetch, '/jobs', {
		view,
		type: params.operation,
		phase: params.status && queueStatuses.has(params.status) ? params.status : undefined,
		limit: 200
	}).then((response) => ({
		jobs: response.items.map((row) => ({
			job_id: row.job_id,
			status: row.status.outcome ?? row.status.phase,
			operation: row.job_type,
			label: row.label,
			media_file_id: row.subject.display_id,
			created_at: row.created_at ?? '',
			updated_at: row.terminal_at ?? row.started_at ?? row.created_at ?? '',
			started_at: row.started_at ?? null,
			completed_at: row.terminal_at ?? null,
			result: null,
			error: null,
			progress: row.progress
				? {
						stage: row.progress.stage_key ?? '',
						percent: row.progress.overall?.percent ?? 0,
						message: row.progress.headline ?? row.progress.stage_label ?? ''
					}
				: null,
			events_url: row.links.snapshot,
			backup_id: null,
			plan: null
		}))
	}));
}

export async function confirmJob(
	fetch: Fetch,
	jobId: string,
	expectedPlanVersion?: string,
	expectedConfigurationVersion?: number
): Promise<{ job_id: string; status: 'queued' }> {
	if (useMocks()) return Promise.resolve({ job_id: jobId, status: 'queued' });
	if (expectedPlanVersion === undefined) {
		const snapshot = await apiGet<components['schemas']['JobSnapshotResponse']>(
			fetch,
			`/jobs/${jobId}/snapshot`
		);
		if (snapshot.phase === 'queued' || snapshot.phase === 'running') {
			return { job_id: jobId, status: 'queued' };
		}
		throw new ApiError(409, 'This operation does not expose a canonical confirmation plan.');
	}
	const result = await apiSend<components['schemas']['JobSubmissionResponse']>(
		fetch,
		'POST',
		`/jobs/${jobId}/mutation-confirmation`,
		{
			expected_plan_version: expectedPlanVersion,
			expected_configuration_version: expectedConfigurationVersion
		}
	);
	if (result.phase === 'queued' || result.phase === 'running') {
		return { job_id: jobId, status: 'queued' };
	}
	throw new ApiError(409, 'The canonical mutation plan was not dispatched.');
}

export async function cancelJob(
	fetch: Fetch,
	jobId: string
): Promise<{ job_id: string; cancel_requested: boolean }> {
	if (useMocks()) return Promise.resolve({ job_id: jobId, cancel_requested: true });
	const snapshot = await cancelCanonicalJob(fetch, jobId);
	return { job_id: snapshot.job_id, cancel_requested: snapshot.cancel_requested };
}
