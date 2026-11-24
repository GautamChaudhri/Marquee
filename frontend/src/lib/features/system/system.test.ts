import { describe, expect, it } from 'vitest';
import type { SystemInfo, WorkerInfo } from '$lib/api/types';
import { countsLine, deployPolicyLine, problems, syncLine } from './system';

function system(overrides: Partial<SystemInfo> = {}): SystemInfo {
	return {
		version: '0.1.0',
		environment: 'development',
		schema: { current: '0005', head: '0005', at_head: true },
		settings: {
			deploy_policy: 'off',
			run_new_movies: false,
			sync_interval_minutes: 360,
			movie_roots: ['/movies'],
			data_dir: '/data'
		},
		library: {
			movies: 1,
			movies_with_file: 1,
			posters: {},
			runs: {},
			masked_runs: {},
			deployments: 0,
			exemplars: {},
			preference_events: 0
		},
		models: { manifest: 'found', files: [] },
		last_sync: null,
		storage: { runs: 0, backups: 0, cache: 0, ml: 0, measured_at: '2026-11-24T12:00:00Z' },
		...overrides
	};
}

const running: WorkerInfo = { running: true, since: null, queued: 0, running_jobs: 0, failed: 0 };

describe('problems', () => {
	it('finds none on a healthy installation', () => {
		expect(problems(system(), running)).toEqual([]);
		expect(problems(system(), null)).toEqual([]);
	});

	it('names a schema behind the code, a missing model, and a stopped worker', () => {
		const found = problems(
			system({
				schema: { current: '0004', head: '0005', at_head: false },
				models: {
					manifest: 'found',
					files: [
						{
							item: 'clip',
							path: 'models/clip.onnx',
							kind: 'file',
							present: false,
							size_bytes: null,
							expected_size_bytes: 3
						}
					]
				}
			}),
			{ ...running, running: false }
		);
		expect(found).toEqual([
			'The database schema is at 0004, and this code needs 0005. Run “marquee db upgrade”.',
			'The model file models/clip.onnx is missing. Run “marquee models fetch”.',
			'No worker is running, so nothing queued will run. Start “marquee worker”.'
		]);
	});

	it('names a missing or unreadable manifest', () => {
		expect(problems(system({ models: { manifest: 'missing', files: [] } }), running)).toEqual([
			'There is no model manifest. Run “marquee models fetch”.'
		]);
		expect(problems(system({ models: { manifest: 'unreadable', files: [] } }), running)).toEqual([
			'The model manifest cannot be read.'
		]);
	});
});

describe('settings in words', () => {
	it('says what the deploy policy does', () => {
		expect(deployPolicyLine('off')).toMatch(/^Off: /);
		expect(deployPolicyLine('missing')).toMatch(/where there is no poster/);
		expect(deployPolicyLine('all')).toMatch(/yours included/);
	});

	it('says how often the library syncs', () => {
		expect(syncLine(360)).toBe('Every 6 h');
		expect(syncLine(90)).toBe('Every 90 min');
		expect(syncLine(0)).toBe('Off');
	});

	it('lists counts by key', () => {
		expect(countsLine({ library: 147, marquee: 1 })).toBe('library 147, marquee 1');
		expect(countsLine({})).toBe('none');
	});
});
