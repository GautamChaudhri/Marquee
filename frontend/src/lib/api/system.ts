import { env } from '$env/dynamic/public';
import { randomUuid } from '$lib/uuid';
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
			integrations: {
				tmdb: { configured: true },
				radarr: {
					configured: true,
					url_configured: true,
					api_key_configured: true,
					path_mapping_configured: false
				},
				sonarr: {
					configured: true,
					url_configured: true,
					api_key_configured: true,
					path_mapping_configured: false
				}
			},
			paths: {
				data_dir: 'data',
				media_roots: [],
				radarr_path_prefix_configured: false,
				radarr_media_path_configured: false,
				sonarr_path_prefix_configured: false,
				sonarr_media_path_configured: false,
				metrics_disk_path_configured: false,
				poster_cache_dir: 'data/cache/posters',
				poster_staging_dir: 'data/staging/posters'
			},
			sync: {
				interval_minutes: 60,
				cooldown_seconds: 30,
				heal_enabled: true,
				heal_interval_minutes: 60,
				webhook_dry_run: true
			},
			posters: { restore_method: 'download', backup_dir: 'data/backups/posters' },
			poster_formats: {
				movie: 'poster.jpg',
				series: 'show.jpg',
				season: 'season{season:02d}.jpg'
			},
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
				integrations: {
					tmdb: { configured: true },
					radarr: {
						configured: true,
						url_configured: true,
						api_key_configured: true,
						path_mapping_configured: false
					},
					sonarr: {
						configured: true,
						url_configured: true,
						api_key_configured: true,
						path_mapping_configured: false
					}
				},
				paths: {
					data_dir: 'data',
					media_roots: [],
					radarr_path_prefix_configured: false,
					radarr_media_path_configured: false,
					sonarr_path_prefix_configured: false,
					sonarr_media_path_configured: false,
					metrics_disk_path_configured: false,
					poster_cache_dir: 'data/cache/posters',
					poster_staging_dir: 'data/staging/posters'
				},
				sync: {
					interval_minutes: 60,
					cooldown_seconds: 30,
					heal_enabled: true,
					heal_interval_minutes: 60,
					webhook_dry_run: true
				},
				posters: { restore_method: 'download', backup_dir: 'data/backups/posters' },
				poster_formats: {
					movie: 'poster.jpg',
					series: 'show.jpg',
					season: 'season{season:02d}.jpg'
				},
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
			phase: 'queued',
			disposition: 'created',
			idempotent: false,
			snapshot_url: '/api/jobs/mock-heal/snapshot',
			detail_url: '/projection-room/jobs/mock-heal',
			activity_url: '/projection-room?view=queue&job=mock-heal',
			active_conflict: null
		});
	}
	return apiSend<JobSubmissionResponse>(
		fetch,
		'POST',
		'/system/heal',
		{},
		{ 'Idempotency-Key': `poster_heal:${randomUuid()}` }
	);
}
