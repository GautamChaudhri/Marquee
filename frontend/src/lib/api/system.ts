import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockMetrics, mockMetricsHistory } from './mock';
import type { RuntimeSettings, SystemMetrics, SystemMetricsHistory } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

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

export function putSettings(fetch: Fetch, payload: any): Promise<any> {
	if (useMocks()) {
		return Promise.resolve({
			applied: Object.keys(payload),
			settings: payload
		});
	}
	return apiSend<any>(fetch, 'PUT', '/settings', payload);
}
