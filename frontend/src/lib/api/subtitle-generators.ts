import { env } from '$env/dynamic/public';
import { apiGet, apiSend, type Fetch } from './client';
import { mockGenerators } from './mock';
import type {
	SubtitleGenerator,
	GenerationRequest,
	SubgenHardwareResponse,
	SubgenSettings
} from './types';

const useMocks = () => env.PUBLIC_USE_MOCKS === 'true';

export function getGenerators(fetch: Fetch): Promise<{ generators: SubtitleGenerator[] }> {
	if (useMocks()) return Promise.resolve(mockGenerators());
	return apiGet<{ generators: SubtitleGenerator[] }>(fetch, '/subtitle-generators');
}

export function submitGeneration(
	fetch: Fetch,
	mediaFileId: number,
	request: GenerationRequest
): Promise<{ job_id: string; events_url: string }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-gen-${Date.now()}`,
			events_url: `/api/jobs/job-mock-gen-${Date.now()}/snapshot`
		});
	}
	return apiSend<{ job_id: string; events_url: string }>(
		fetch,
		'POST',
		`/media-files/${mediaFileId}/subtitle-generations`,
		request
	);
}

export function submitMovieGeneration(
	fetch: Fetch,
	movieId: number,
	request: GenerationRequest
): Promise<{ job_id: string; events_url: string }> {
	if (useMocks()) {
		return Promise.resolve({
			job_id: `job-mock-gen-${Date.now()}`,
			events_url: `/api/jobs/job-mock-gen-${Date.now()}/snapshot`
		});
	}
	return apiSend<{ job_id: string; events_url: string }>(
		fetch,
		'POST',
		`/movies/${movieId}/subtitle-generations`,
		request
	);
}

export function getSubgenHardware(fetch: Fetch): Promise<SubgenHardwareResponse> {
	if (useMocks()) {
		return Promise.resolve({
			hardware: {
				gpus: [
					{
						index: 0,
						name: 'NVIDIA GeForce RTX 4080',
						vram_total: 17179869184,
						vram_free: 16106127360
					}
				],
				cpu_count: 16,
				ram_total: 34359738368
			},
			models: {
				tiny: { supported: true, verdict: 'supported', vram_estimate_gb: 0.5, note: 'supported' },
				base: { supported: true, verdict: 'supported', vram_estimate_gb: 0.7, note: 'supported' },
				small: { supported: true, verdict: 'supported', vram_estimate_gb: 1.2, note: 'supported' },
				medium: { supported: true, verdict: 'supported', vram_estimate_gb: 2.6, note: 'supported' },
				'distil-large-v3': {
					supported: true,
					verdict: 'supported',
					vram_estimate_gb: 1.9,
					note: 'fastest for English'
				},
				'large-v3-turbo': {
					supported: true,
					verdict: 'supported',
					vram_estimate_gb: 1.9,
					note: 'recommended default'
				},
				'large-v3': {
					supported: true,
					verdict: 'supported',
					vram_estimate_gb: 4.7,
					note: 'highest accuracy'
				},
				custom: { supported: true, verdict: 'supported', vram_estimate_gb: 0.0, note: 'custom' }
			},
			catalog: [
				{
					id: 'tiny',
					params: '39M',
					vram_fp16_gb: 0.5,
					vram_int8_gb: 0.3,
					multilingual: true,
					can_translate: true,
					notes: 'last resort'
				},
				{
					id: 'base',
					params: '74M',
					vram_fp16_gb: 0.7,
					vram_int8_gb: 0.4,
					multilingual: true,
					can_translate: true,
					notes: 'weak CPUs'
				},
				{
					id: 'small',
					params: '244M',
					vram_fp16_gb: 1.2,
					vram_int8_gb: 0.8,
					multilingual: true,
					can_translate: true,
					notes: 'CPU default',
					cpu_ram_int8_gb: 1.5
				},
				{
					id: 'medium',
					params: '769M',
					vram_fp16_gb: 2.6,
					vram_int8_gb: 1.6,
					multilingual: true,
					can_translate: true,
					notes: 'legacy mid-size'
				},
				{
					id: 'distil-large-v3',
					params: '756M',
					vram_fp16_gb: 1.9,
					vram_int8_gb: 1.2,
					multilingual: false,
					can_translate: false,
					notes: 'fastest for English libraries'
				},
				{
					id: 'large-v3-turbo',
					params: '809M',
					vram_fp16_gb: 1.9,
					vram_int8_gb: 1.3,
					multilingual: true,
					can_translate: false,
					notes: 'best speed/accuracy; default GPU pick'
				},
				{
					id: 'large-v3',
					params: '1550M',
					vram_fp16_gb: 4.7,
					vram_int8_gb: 3.0,
					multilingual: true,
					can_translate: true,
					notes: 'max accuracy'
				},
				{
					id: 'custom',
					params: '-',
					vram_fp16_gb: null,
					vram_int8_gb: null,
					multilingual: null,
					can_translate: null,
					notes: 'free-text repo or path'
				}
			],
			recommendation: {
				model: 'large-v3-turbo',
				device: 'cuda',
				gpu_index: 0,
				compute_type: 'float16',
				reason: 'Optimal GPU vram budget'
			}
		});
	}
	return apiGet<SubgenHardwareResponse>(fetch, '/subtitle-generators/subgen/hardware');
}

export function putSubgenSettings(
	fetch: Fetch,
	settings: SubgenSettings,
	expectedVersion: number
): Promise<{
	configuration_version: number;
	etag: string;
	changed: boolean;
	applied: string[];
	settings: Record<string, unknown>;
}> {
	if (useMocks()) {
		return Promise.resolve({
			configuration_version: expectedVersion + 1,
			etag: `"configuration-${expectedVersion + 1}"`,
			changed: true,
			applied: Object.keys(settings),
			settings: settings as Record<string, unknown>
		});
	}
	return apiSend<{
		configuration_version: number;
		etag: string;
		changed: boolean;
		applied: string[];
		settings: Record<string, unknown>;
	}>(fetch, 'PUT', '/subtitle-generators/subgen/settings', {
		...settings,
		expected_version: expectedVersion
	});
}

export function restartSubgen(fetch: Fetch): Promise<{ ok: boolean }> {
	if (useMocks()) {
		return Promise.resolve({ ok: true });
	}
	return apiSend<{ ok: boolean }>(fetch, 'POST', '/subtitle-generators/subgen/restart');
}

export function getSubgenLogs(fetch: Fetch, tail: number = 200): Promise<{ lines: string[] }> {
	if (useMocks()) {
		return Promise.resolve({
			lines: [
				'2026-07-04 16:30:00 [info] Subgen loaded.',
				'2026-07-04 16:30:05 [info] Loading Whisper model large-v3-turbo on CUDA:0...',
				'2026-07-04 16:30:12 [info] Whisper model loaded successfully.'
			]
		});
	}
	return apiGet<{ lines: string[] }>(fetch, `/subtitle-generators/subgen/logs?tail=${tail}`);
}

export function testSubgen(fetch: Fetch): Promise<{
	ok: boolean;
	detected_language: string;
	latency_ms: number;
	raw: Record<string, unknown>;
}> {
	if (useMocks()) {
		return Promise.resolve({
			ok: true,
			detected_language: 'en',
			latency_ms: 124.5,
			raw: {}
		});
	}
	return apiSend<{
		ok: boolean;
		detected_language: string;
		latency_ms: number;
		raw: Record<string, unknown>;
	}>(fetch, 'POST', '/subtitle-generators/subgen/test');
}
