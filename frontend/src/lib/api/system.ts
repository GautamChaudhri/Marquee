import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockMetrics, mockMetricsHistory } from './mock';
import type { JobSummary, RuntimeSettings, SystemMetrics, SystemMetricsHistory } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';
type SettingsPatch = Record<string, unknown>;
type SettingsPutResponse = { applied: string[]; settings: RuntimeSettings };
type HealScanResponse = JobSummary & { checked?: number; restored?: number; failed?: number };

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

export function putSettings(fetch: Fetch, payload: SettingsPatch): Promise<SettingsPutResponse> {
	if (useMocks()) {
		return Promise.resolve({
			applied: Object.keys(payload),
			settings: {
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
	return apiSend<SettingsPutResponse>(fetch, 'PUT', '/settings', payload);
}

export function runHealScan(fetch: Fetch): Promise<HealScanResponse> {
	if (useMocks()) {
		const now = new Date().toISOString();
		return Promise.resolve({
			job_id: 'mock-heal',
			type: 'poster_heal',
			status: 'succeeded',
			stage: 'done',
			progress: { percent: 100 },
			message: 'Mock heal finished',
			subject: { type: 'maintenance', id: 'poster-heal' },
			cancel_requested: false,
			events_url: '/api/jobs/mock-heal/events',
			status_url: '/api/jobs/mock-heal',
			created_at: now,
			updated_at: now,
			checked: 1,
			restored: 0,
			failed: 0
		});
	}
	return apiSend<HealScanResponse>(fetch, 'POST', '/system/heal');
}
