import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockMetrics } from './mock';
import type { SystemMetrics } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getMetrics(fetch: Fetch): Promise<SystemMetrics> {
	if (useMocks()) return Promise.resolve(mockMetrics());
	return apiGet<SystemMetrics>(fetch, '/system/metrics');
}

export function getStatus(fetch: Fetch): Promise<Record<string, unknown>> {
	return apiGet<Record<string, unknown>>(fetch, '/system/status');
}

export function getSettings(fetch: Fetch): Promise<any> {
	if (useMocks()) {
		return Promise.resolve({
			subtitles: {
				enabled: true,
				scan_concurrency: 2,
				mutation_concurrency: 1,
				generation_concurrency: 1,
				preferred_languages: ['en'],
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
			}
		});
	}
	return apiGet<any>(fetch, '/settings');
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

