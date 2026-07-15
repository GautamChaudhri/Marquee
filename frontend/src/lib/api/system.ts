import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import { mockMetrics, mockMetricsHistory } from './mock';
import type { RuntimeSettings, SystemMetrics, SystemMetricsHistory } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';
type SettingsPatch = Record<string, unknown>;
export type SettingsPutResponse = {
	configuration_version: number;
	etag: string;
	changed: boolean;
	applied: string[];
	settings: RuntimeSettings;
};
type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];

export function getMetrics(fetch: Fetch): Promise<SystemMetrics> {
	if (useMocks()) return Promise.resolve(mockMetrics());
	return apiGet<SystemMetrics>(fetch, '/system/metrics');
}

export function getMetricsHistory(
	fetch: Fetch,
	params: { window?: '15m' | '1h' | '6h' | '24h'; resolution?: number } = {}
): Promise<SystemMetricsHistory> {
	if (useMocks()) return Promise.resolve(mockMetricsHistory(params.window ?? '1h'));
	return apiGet<SystemMetricsHistory>(fetch, '/system/metrics/history', params);
}

export function getStatus(fetch: Fetch): Promise<Record<string, unknown>> {
	return apiGet<Record<string, unknown>>(fetch, '/system/status');
}

export function getSettings(fetch: Fetch): Promise<RuntimeSettings> {
	if (useMocks()) {
		return Promise.resolve({
			configuration_version: 1,
			etag: '"configuration-1"',
			stale: false,
			health: { status: 'valid', version: 1 },
			configuration_meta: {},
			app: {
				name: 'Marquee',
				host: '0.0.0.0',
				port: 3165,
				debug: true,
				log_level: 'INFO',
				log_format: 'text',
				cors_origins: ['http://localhost:5173'],
				auth: {
					api_key_configured: false,
					allow_local: true
				}
			},
			subtitles: {
				enabled: true,
				scan_concurrency: 2,
				mutation_concurrency: 1,
				generation_concurrency: 1,
				preferred_languages: ['en'],
				preferred_audio_languages: null,
				preferred_subtitle_languages: null,
				effective_preferred_audio_languages: ['en'],
				effective_preferred_subtitle_languages: ['en'],
				unknown_language_action: 'review',
				protect_forced: true,
				protect_last_full_dialogue: true,
				backup_mode: 'keep_original',
				external_delete_mode: 'quarantine'
			},
			integrations: {
				subgen: {
					configured: true,
					deployment: 'external',
					url_configured: true,
					callback_token_configured: true,
					url: 'http://localhost:9000',
					profile_name: 'faster-whisper',
					model_label: 'medium',
					mode: 'transcribe'
				}
			},
			paths: {},
			sync: {},
			letterbox: {},
			posters: { restore_method: 'download', backup_dir: 'data/backups/posters' },
			poster_formats: {},
			writable: true
		});
	}
	return apiGet<RuntimeSettings>(fetch, '/settings');
}

export function putSettings(
	fetch: Fetch,
	payload: SettingsPatch,
	expectedVersion: number
): Promise<SettingsPutResponse> {
	if (useMocks()) {
		return Promise.resolve({
			configuration_version: expectedVersion + 1,
			etag: `"configuration-${expectedVersion + 1}"`,
			changed: Object.keys(payload).length > 0,
			applied: Object.keys(payload),
			settings: {
				configuration_version: expectedVersion + 1,
				etag: `"configuration-${expectedVersion + 1}"`,
				stale: false,
				health: { status: 'valid', version: expectedVersion + 1 },
				configuration_meta: {},
				app: {
					name: 'Marquee',
					host: '0.0.0.0',
					port: 3165,
					debug: true,
					log_level: 'INFO',
					log_format: 'text',
					cors_origins: ['http://localhost:5173'],
					auth: {
						api_key_configured: false,
						allow_local: true
					}
				},
				subtitles: {
					enabled: true,
					scan_concurrency: 2,
					mutation_concurrency: 1,
					generation_concurrency: 1,
					preferred_languages: ['en'],
					preferred_audio_languages: null,
					preferred_subtitle_languages: null,
					effective_preferred_audio_languages: ['en'],
					effective_preferred_subtitle_languages: ['en'],
					unknown_language_action: 'review',
					protect_forced: true,
					protect_last_full_dialogue: true,
					backup_mode: 'keep_original',
					external_delete_mode: 'quarantine'
				},
				integrations: {
					subgen: {
						configured: true,
						deployment: 'external',
						url_configured: true,
						callback_token_configured: true,
						url: 'http://localhost:9000',
						profile_name: 'faster-whisper',
						model_label: 'medium',
						mode: 'transcribe'
					}
				},
				paths: {},
				sync: {},
				letterbox: {},
				posters: { restore_method: 'download', backup_dir: 'data/backups/posters' },
				poster_formats: {},
				writable: true
			}
		});
	}
	return apiSend<SettingsPutResponse>(fetch, 'PUT', '/settings', {
		...payload,
		expected_version: expectedVersion
	});
}

export function runHealScan(fetch: Fetch): Promise<JobSubmissionResponse> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: 'mock-heal',
			job_type: 'poster_heal',
			phase: 'queued',
			disposition: 'created',
			snapshot_url: '/api/jobs/mock-heal/snapshot',
			detail_url: '/api/jobs/mock-heal'
		});
	}
	return apiSend<JobSubmissionResponse>(
		fetch,
		'POST',
		'/system/heal',
		{},
		{ 'Idempotency-Key': `poster_heal:${crypto.randomUUID()}` }
	);
}
