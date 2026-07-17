import { env } from '$env/dynamic/public';
import type { JobSubmissionResponse } from '$lib/activity/types';
import { apiGet, apiSend, type Fetch } from './client';
import { mockPolicies } from './mock';
import type { SubtitlePolicy } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function listPolicies(fetch: Fetch): Promise<{ policies: SubtitlePolicy[] }> {
	if (useMocks()) return Promise.resolve(mockPolicies());
	return apiGet<{ policies: SubtitlePolicy[] }>(fetch, '/subtitle-policies');
}

export function getPolicy(fetch: Fetch, id: number): Promise<SubtitlePolicy> {
	if (useMocks()) {
		const policy = mockPolicies().policies.find((p) => p.id === id) ?? mockPolicies().policies[0];
		return Promise.resolve(policy);
	}
	return apiGet<SubtitlePolicy>(fetch, `/subtitle-policies/${id}`);
}

export function createPolicy(fetch: Fetch, body: Partial<SubtitlePolicy>): Promise<SubtitlePolicy> {
	if (useMocks()) {
		const newPolicy: SubtitlePolicy = {
			id: Math.floor(Math.random() * 1000),
			name: body.name ?? 'Unnamed Policy',
			enabled: body.enabled ?? true,
			revision: 1,
			mode: body.mode ?? 'allowlist',
			languages: body.languages ?? ['en'],
			unknown_action: body.unknown_action ?? 'review',
			protect_forced: body.protect_forced ?? true,
			protect_default: body.protect_default ?? true,
			protect_last_full_dialogue: body.protect_last_full_dialogue ?? true,
			include_external: body.include_external ?? true,
			auto_apply: body.auto_apply ?? false,
			audit_only: body.audit_only ?? false,
			hardlink_action: body.hardlink_action ?? 'block',
			backup_mode: body.backup_mode ?? 'keep_original',
			created_at: new Date().toISOString(),
			updated_at: new Date().toISOString()
		};
		return Promise.resolve(newPolicy);
	}
	return apiSend<SubtitlePolicy>(fetch, 'POST', '/subtitle-policies', body);
}

export function updatePolicy(
	fetch: Fetch,
	id: number,
	body: Partial<SubtitlePolicy>
): Promise<SubtitlePolicy> {
	if (useMocks()) {
		const existing = mockPolicies().policies.find((p) => p.id === id) ?? mockPolicies().policies[0];
		return Promise.resolve({
			...existing,
			...body,
			revision: existing.revision + 1,
			updated_at: new Date().toISOString()
		});
	}
	return apiSend<SubtitlePolicy>(fetch, 'PUT', `/subtitle-policies/${id}`, body);
}

export function deletePolicy(fetch: Fetch, id: number): Promise<{ deleted: number }> {
	if (useMocks()) return Promise.resolve({ deleted: id });
	return apiSend<{ deleted: number }>(fetch, 'DELETE', `/subtitle-policies/${id}`);
}

export function auditPolicy(
	fetch: Fetch,
	id: number,
	scope: 'all' | 'movies' | 'tv' = 'all'
): Promise<JobSubmissionResponse> {
	if (useMocks()) {
		return Promise.resolve({
			detail_url: `/projection-room/jobs/job-mock-policy-audit-${id}`,
			disposition: 'created',
			idempotent: false,
			job_id: `job-mock-policy-audit-${id}`,
			phase: 'queued',
			snapshot_url: `/api/jobs/job-mock-policy-audit-${id}/snapshot`,
			activity_url: `/projection-room?view=queue&job=job-mock-policy-audit-${id}`,
			active_conflict: null
		});
	}
	return apiSend<JobSubmissionResponse>(fetch, 'POST', `/subtitle-policies/${id}/audit`, {
		scope
	});
}

export function applyPolicy(
	fetch: Fetch,
	id: number,
	movieIds: number[]
): Promise<JobSubmissionResponse> {
	if (useMocks()) {
		return Promise.resolve({
			detail_url: '/projection-room/jobs/job-mock-policy',
			job_id: `job-mock-policy-${Date.now()}`,
			disposition: 'created',
			idempotent: false,
			phase: 'queued',
			snapshot_url: '/api/jobs/job-mock-policy/snapshot',
			activity_url: '/projection-room?view=queue&job=job-mock-policy',
			active_conflict: null
		});
	}
	return apiSend<JobSubmissionResponse>(
		fetch,
		'POST',
		`/subtitle-policies/${id}/apply`,
		{ movie_ids: movieIds },
		{ 'Idempotency-Key': `subtitle_policy_batch:${crypto.randomUUID()}` }
	);
}
