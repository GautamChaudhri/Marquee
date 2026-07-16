import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import {
	IncompatibleResponseError,
	jobApiErrorCode,
	parseCommandResponse,
	parseJobEventFrame,
	parseJobSubmission,
	parseListEnvelope,
	parseSnapshot
} from './validators';

const snapshot = {
	version: 1,
	job_id: 'j1',
	type: 'poster_pipeline',
	phase: 'running',
	outcome: null,
	desired_state: 'run',
	fence_token: 3,
	progress_sequence: 7,
	progress: null,
	links: {}
};

describe('parseListEnvelope', () => {
	it('accepts a well-formed cursor envelope', () => {
		const value = parseListEnvelope({ items: [], limit: 50, next_cursor: null }, 'job list');
		expect(value.items).toEqual([]);
		expect(value.next_cursor).toBeNull();
	});

	it('rejects a missing or non-array items field', () => {
		expect(() => parseListEnvelope({ limit: 50, next_cursor: null }, 'job list')).toThrow(
			IncompatibleResponseError
		);
	});

	it('rejects a non-string, non-null cursor', () => {
		expect(() => parseListEnvelope({ items: [], limit: 50, next_cursor: 5 }, 'job list')).toThrow(
			IncompatibleResponseError
		);
	});
});

describe('parseSnapshot', () => {
	it('accepts a valid snapshot and preserves a null terminal outcome', () => {
		const value = parseSnapshot(snapshot);
		expect(value.job_id).toBe('j1');
		expect(value.outcome).toBeNull();
	});

	it('accepts a terminal outcome without forcing it', () => {
		const value = parseSnapshot({ ...snapshot, phase: 'terminal', outcome: 'succeeded' });
		expect(value.outcome).toBe('succeeded');
	});

	it('rejects a snapshot missing its fence token', () => {
		const invalid = {
			job_id: 'j1',
			type: 'poster_pipeline',
			phase: 'running',
			outcome: null,
			desired_state: 'run',
			progress_sequence: 7,
			progress: null
		};
		expect(() => parseSnapshot(invalid)).toThrow(IncompatibleResponseError);
	});

	it('rejects a newer snapshot schema version', () => {
		expect(() => parseSnapshot({ ...snapshot, version: 2 })).toThrow(IncompatibleResponseError);
	});
});

describe('parseCommandResponse', () => {
	it('accepts a command response with a valid embedded snapshot', () => {
		const value = parseCommandResponse({
			action: 'cancel',
			execution_class: 'media_write',
			snapshot
		});
		expect(value.action).toBe('cancel');
	});

	it('rejects a command response whose snapshot is malformed', () => {
		expect(() =>
			parseCommandResponse({ action: 'cancel', execution_class: 'media_write', snapshot: {} })
		).toThrow(IncompatibleResponseError);
	});

	it('rejects a command response missing the action', () => {
		expect(() => parseCommandResponse({ execution_class: 'x', snapshot })).toThrow(
			IncompatibleResponseError
		);
	});
});

describe('parseJobSubmission', () => {
	it('accepts a valid submission response', () => {
		const value = parseJobSubmission({
			job_id: 'j1',
			phase: 'queued',
			disposition: 'created',
			snapshot_url: '/api/jobs/j1/snapshot',
			detail_url: '/projection-room/jobs/j1'
		});
		expect(value.job_id).toBe('j1');
	});

	it('rejects a submission missing its detail url', () => {
		expect(() =>
			parseJobSubmission({
				job_id: 'j1',
				phase: 'queued',
				disposition: 'created',
				snapshot_url: '/api/jobs/j1/snapshot'
			})
		).toThrow(IncompatibleResponseError);
	});
});

describe('parseJobEventFrame', () => {
	const frame = {
		version: 1,
		cursor: 12,
		canonical_version: 4,
		event_key: 'progress.updated',
		job_id: 'j1',
		reconciliation: null,
		delta: { state: 'running' }
	};

	it('accepts a version-1 frame', () => {
		expect(parseJobEventFrame(frame).cursor).toBe(12);
	});

	it('accepts a stream-level frame with a null job_id', () => {
		expect(parseJobEventFrame({ ...frame, job_id: null }).job_id).toBeNull();
	});

	it('rejects an unknown frame schema version as incompatible', () => {
		expect(() => parseJobEventFrame({ ...frame, version: 2 })).toThrow(/version/);
	});

	it('rejects a frame whose delta lacks a state', () => {
		expect(() => parseJobEventFrame({ ...frame, delta: {} })).toThrow(IncompatibleResponseError);
	});
});

describe('jobApiErrorCode', () => {
	it('reads the structured detail code', () => {
		const error = new ApiError(409, 'conflict', { detail: { code: 'stale_command' } });
		expect(jobApiErrorCode(error)).toBe('stale_command');
	});

	it('falls back to a top-level code', () => {
		const error = new ApiError(413, 'too big', { code: 'request_body_too_large' });
		expect(jobApiErrorCode(error)).toBe('request_body_too_large');
	});

	it('returns null for a non-API error', () => {
		expect(jobApiErrorCode(new Error('boom'))).toBeNull();
	});
});
