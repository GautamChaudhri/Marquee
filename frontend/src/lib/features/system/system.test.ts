import { describe, expect, it } from 'vitest';
import type { SystemInfo, WorkerInfo } from '#lib/api/types.ts';
import {
	backupLine,
	backupSettingLine,
	countsLine,
	deployPolicyLine,
	pathsNote,
	problems,
	retentionLine,
	retentionSettingLine,
	syncLine
} from './system';

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
		backups: {
			interval_hours: 24,
			keep: 14,
			count: 2,
			total_bytes: 3072,
			newest_at: '2026-11-24T09:00:00Z',
			newest_size_bytes: 2048,
			stale: false,
			last_job: null
		},
		retention: {
			interval_hours: 24,
			days: 90,
			last_job: null,
			freed_items: null,
			freed_bytes: null,
			freed_at: null
		},
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

describe('whose paths', () => {
	it('names the host folders in production, where the paths are the containers’', () => {
		expect(pathsNote('production')).toContain('Paths inside Marquee’s containers');
		expect(pathsNote('production')).toContain('MOVIE_LIBRARY_HOST_PATH');
		expect(pathsNote('production')).toContain('MARQUEE_DATA_HOST_PATH');
		expect(pathsNote('development')).toBeNull();
	});
});

describe('backups and retention', () => {
	const job = (state: 'failed' | 'succeeded', job_id: number) => ({
		job_id,
		state,
		created_at: '2026-11-24T12:00:00Z',
		finished_at: '2026-11-24T12:00:00Z'
	});

	it('names an overdue or missing dump and a failed backup or retention job', () => {
		const base = system();
		const found = problems(
			system({
				backups: { ...base.backups, stale: true, last_job: job('failed', 42) },
				retention: { ...base.retention, last_job: job('failed', 43) }
			}),
			running
		);
		expect(found).toHaveLength(3);
		expect(found[0]).toMatch(/older than two intervals \(48 h\)\. Is the backup job failing\?$/);
		expect(found[1]).toBe(
			'The last database backup (job 42) failed. Its error is on the Activity page.'
		);
		expect(found[2]).toBe(
			'The last retention pass (job 43) failed. Its error is on the Activity page.'
		);
		const none = problems(
			system({ backups: { ...base.backups, stale: true, newest_at: null, count: 0 } }),
			running
		);
		expect(none).toEqual([
			'No database dump has been made yet. The worker makes one when it starts; “marquee db backup” makes one now.'
		]);
		expect(
			problems(system({ backups: { ...base.backups, last_job: job('succeeded', 40) } }), running)
		).toEqual([]);
	});

	it('says the newest dump, what retention freed, and both settings in words', () => {
		const base = system();
		expect(backupLine(base.backups)).toMatch(/^Newest .*\(2\.0 KB\); 2 dumps, 3\.0 KB in all$/);
		expect(backupLine({ ...base.backups, newest_at: null, count: 0 })).toBe('No dump yet');
		expect(retentionLine(base.retention)).toBe(
			'Not run yet: the first pass is one interval (24 h) after the worker first starts with it'
		);
		expect(
			retentionLine({
				...base.retention,
				freed_items: 1,
				freed_bytes: 1024,
				freed_at: '2026-11-25T12:00:00Z'
			})
		).toMatch(/^Last pass .*: deleted 1 item, 1.0 KB$/);
		expect(backupSettingLine(base.backups)).toBe('Every 24 h; the newest 14 kept');
		expect(backupSettingLine({ ...base.backups, interval_hours: 0 })).toBe(
			'Off: only the dumps you make with “marquee db backup”'
		);
		expect(retentionSettingLine(base.retention)).toBe(
			'Every 24 h; finished jobs and unneeded runs after 90 days'
		);
		expect(retentionSettingLine({ ...base.retention, interval_hours: 0 })).toBe(
			'Off: nothing is deleted by itself'
		);
	});

	it('says the missing policy leaves your choices alone', () => {
		expect(deployPolicyLine('missing')).toMatch(/never over a poster you chose/);
	});
});
