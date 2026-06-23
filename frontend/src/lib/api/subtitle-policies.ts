import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockPolicies } from './mock';
import type { SubtitlePolicy, PolicyAuditResult } from './types';

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
	movieIds: number[]
): Promise<PolicyAuditResult> {
	if (useMocks()) {
		return Promise.resolve({
			policy_id: id,
			total_removals: 2,
			items: [
				{
					movie_id: movieIds[0] ?? 1,
					media_file_id: `mf-${movieIds[0] ?? 1}`,
					removals: 1,
					protected: 1,
					review_required: 0,
					warnings: [],
					coverage_before: {
						audio_languages: ['en'],
						full_dialogue_languages: ['en', 'fr'],
						forced_only_languages: [],
						sdh_languages: [],
						commentary_present: false,
						external_present: false,
						embedded_present: true,
						generated_present: false,
						unknown_present: false,
						missing_preferred_languages: [],
						track_count: 2
					},
					coverage_after: {
						audio_languages: ['en'],
						full_dialogue_languages: ['en'],
						forced_only_languages: [],
						sdh_languages: [],
						commentary_present: false,
						external_present: false,
						embedded_present: true,
						generated_present: false,
						unknown_present: false,
						missing_preferred_languages: [],
						track_count: 1
					}
				}
			]
		});
	}
	return apiSend<PolicyAuditResult>(fetch, 'POST', `/subtitle-policies/${id}/audit`, {
		movie_ids: movieIds
	});
}

export function applyPolicy(
	fetch: Fetch,
	id: number,
	movieIds: number[]
): Promise<{ batch_id: number; queued: number; skipped: number }> {
	if (useMocks()) {
		return Promise.resolve({
			batch_id: Math.floor(Math.random() * 100),
			queued: movieIds.length,
			skipped: 0
		});
	}
	return apiSend<{ batch_id: number; queued: number; skipped: number }>(
		fetch,
		'POST',
		`/subtitle-policies/${id}/apply`,
		{ movie_ids: movieIds }
	);
}
