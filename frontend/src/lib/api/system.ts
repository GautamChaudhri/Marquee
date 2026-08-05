import { env } from '$env/dynamic/public';
import { randomUuid } from '$lib/uuid';
import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import { mockMetrics, mockMetricsHistory } from './mock';
import type { RuntimeSettings, SystemMetrics, SystemMetricsHistory } from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';
type SettingsPatch = Record<string, unknown>;
export type IntegrationProvider = 'tmdb' | 'radarr' | 'sonarr';
export type PathMappingTestResult = {
	ok: boolean;
	mutated: false;
	path_mappings: Record<
		'radarr' | 'sonarr',
		Array<{
			configured: boolean;
			prefix: string | null;
			target: {
				path: string;
				exists: boolean;
				directory: boolean;
				readable: boolean;
				writable: boolean;
			} | null;
		}>
	>;
	mappings: Record<
		'radarr' | 'sonarr',
		{
			configured: boolean;
			prefix: string | null;
			target: {
				path: string;
				exists: boolean;
				directory: boolean;
				readable: boolean;
				writable: boolean;
			} | null;
		}
	>;
	media_roots: Array<{
		path: string;
		exists: boolean;
		directory: boolean;
		readable: boolean;
		writable: boolean;
	}>;
};
export type SettingsPutResponse = {
	configuration_version: number;
	etag: string;
	changed: boolean;
	applied?: string[];
	status?: IntegrationStatusFacts;
	settings: RuntimeSettings;
};
export type ConfigurationResetPayload = {
	expected_version: number;
	scope: 'all' | 'tab' | 'section' | 'keys';
	tab?: string;
	section?: string;
	keys?: string[];
};
/** Whitelisted identifying facts the server reads back from a service probe. */
export type IntegrationStatusFacts = {
	version?: string;
	appName?: string;
	instanceName?: string;
	osName?: string;
	osVersion?: string;
	runtimeVersion?: string;
	isDocker?: boolean;
	startTime?: string;
	imageBaseUrl?: string;
};
type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];

function mockSettings(version = 1): RuntimeSettings {
	return {
		configuration_version: version,
		etag: `"configuration-${version}"`,
		stale: false,
		health: { status: 'valid', version },
		catalog: {},
		values: {},
		defaults: {},
		sources: {},
		secrets: {},
		secret_store: { writable: false, reason: 'Mock secret store' },
		deployment: {
			version: 'dev',
			environment: 'development',
			process_role: 'api',
			host: '0.0.0.0',
			port: 3165,
			debug: true,
			database_configured: true,
			api_key_configured: false,
			keyring_configured: false,
			mounts: []
		},
		configuration_meta: {},
		app: {
			name: 'Marquee',
			host: '0.0.0.0',
			port: 3165,
			debug: true,
			log_level: 'INFO',
			log_format: 'text',
			cors_origins: ['http://localhost:5173'],
			auth: { api_key_configured: false, allow_local: true }
		},
		integrations: {
			tmdb: { configured: true, name: 'The Movie Database' },
			radarr: {
				configured: true,
				name: 'Radarr',
				url_configured: true,
				api_key_configured: true,
				path_mapping_configured: false
			},
			sonarr: {
				configured: true,
				name: 'Sonarr',
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
	};
}

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

/** The OCR slice of `/system/status`, typed because the pipeline page renders it. */
export interface OcrGpu {
	index: number;
	name: string;
	vram_total: number;
	vram_free: number;
}

export interface OcrStatus {
	device: string;
	configured_workers: number;
	effective_workers: number;
	workers: { active: Array<{ pid: number; name: string }>; stale_reaped: unknown[] };
	plan: {
		requested: string;
		gpu_build: boolean;
		gpus: OcrGpu[];
		/** null when the request cannot be satisfied — see `error`. */
		expected_device: string | null;
		error: string | null;
		/** false in auto mode: predicted from the wheel + NVML, not observed. */
		confirmed: boolean;
	};
}

export async function getOcrStatus(fetch: Fetch): Promise<OcrStatus | null> {
	const status = await getStatus(fetch);
	const ocr = status.ocr;
	// Older backends have no `plan`; the panel renders nothing rather than half of itself.
	if (!ocr || typeof ocr !== 'object' || !('plan' in ocr)) return null;
	return ocr as OcrStatus;
}

export function getSettings(fetch: Fetch): Promise<RuntimeSettings> {
	if (useMocks()) return Promise.resolve(mockSettings());
	return apiGet<RuntimeSettings>(fetch, '/settings');
}

export function putConfiguration(
	fetch: Fetch,
	values: Record<string, unknown>,
	expectedVersion: number,
	removals: string[] = []
): Promise<SettingsPutResponse> {
	if (useMocks()) {
		return Promise.resolve({
			configuration_version: expectedVersion + 1,
			etag: `"configuration-${expectedVersion + 1}"`,
			changed: Object.keys(values).length + removals.length > 0,
			applied: [...Object.keys(values), ...removals],
			settings: mockSettings(expectedVersion + 1)
		});
	}
	return apiSend<SettingsPutResponse>(fetch, 'PUT', '/settings/config', {
		expected_version: expectedVersion,
		values,
		removals
	});
}

/**
 * Drop stored overrides so the server's own defaults apply again. This is not
 * the same as writing the default value, which would leave every key marked as
 * a custom override.
 */
export function resetConfiguration(
	fetch: Fetch,
	payload: ConfigurationResetPayload
): Promise<SettingsPutResponse> {
	if (useMocks()) {
		return Promise.resolve({
			configuration_version: payload.expected_version + 1,
			etag: `"configuration-${payload.expected_version + 1}"`,
			changed: true,
			applied: payload.keys ?? [],
			settings: mockSettings(payload.expected_version + 1)
		});
	}
	return apiSend<SettingsPutResponse>(fetch, 'POST', '/settings/config/reset', payload);
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
			settings: mockSettings(expectedVersion + 1)
		});
	}
	return apiSend<SettingsPutResponse>(fetch, 'PUT', '/settings', {
		...payload,
		expected_version: expectedVersion
	});
}

export function testIntegration(
	fetch: Fetch,
	provider: IntegrationProvider,
	payload: { url?: string; credential?: string }
): Promise<{ ok: boolean; provider: string; status?: IntegrationStatusFacts }> {
	return apiSend(fetch, 'POST', `/settings/integrations/${provider}/test`, payload);
}

export function putIntegration(
	fetch: Fetch,
	provider: IntegrationProvider,
	payload: {
		expected_version: number;
		expected_secret_generation: number;
		name?: string;
		url?: string;
		credential?: string;
	}
): Promise<SettingsPutResponse> {
	return apiSend(fetch, 'PUT', `/settings/integrations/${provider}`, payload);
}

export function clearIntegrationCredential(
	fetch: Fetch,
	provider: IntegrationProvider,
	expectedGeneration: number
): Promise<{ cleared: boolean; settings: RuntimeSettings }> {
	return apiSend(fetch, 'DELETE', `/settings/integrations/${provider}/credential`, {
		expected_generation: expectedGeneration
	});
}

export function testPathMappings(
	fetch: Fetch,
	payload: {
		media_roots: string[];
		radarr_path_prefix?: string;
		radarr_media_path?: string;
		radarr_mappings?: Array<{ arr_path: string; marquee_path: string }>;
		sonarr_path_prefix?: string;
		sonarr_media_path?: string;
		sonarr_mappings?: Array<{ arr_path: string; marquee_path: string }>;
	}
): Promise<PathMappingTestResult> {
	return apiSend(fetch, 'POST', '/settings/paths/test', payload);
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
