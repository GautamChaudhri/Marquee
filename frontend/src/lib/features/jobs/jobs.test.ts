import { describe, expect, it } from 'vitest';
import {
	canCancel,
	canRetry,
	describeJob,
	detailHeadline,
	isFinal,
	kindLabel,
	subjectLabel
} from './jobs';

const base = { progress: null, error: null, result: null, cancel_requested: false };

describe('jobs', () => {
	it('know which states are final', () => {
		expect(['succeeded', 'failed', 'cancelled'].every((s) => isFinal(s as never))).toBe(true);
		expect(['queued', 'running'].some((s) => isFinal(s as never))).toBe(false);
	});

	it('offer Cancel and Retry exactly where the API allows them', () => {
		expect(canCancel({ state: 'queued', cancel_requested: false })).toBe(true);
		expect(canCancel({ state: 'running', cancel_requested: false })).toBe(true);
		expect(canCancel({ state: 'running', cancel_requested: true })).toBe(false);
		expect(canCancel({ state: 'succeeded', cancel_requested: false })).toBe(false);
		expect(canRetry({ state: 'failed' })).toBe(true);
		expect(canRetry({ state: 'cancelled' })).toBe(true);
		expect(canRetry({ state: 'succeeded' })).toBe(false);
		expect(canRetry({ state: 'running' })).toBe(false);
	});

	it('describe a job in one line', () => {
		expect(describeJob({ ...base, state: 'queued' })).toBe('Queued');
		expect(describeJob({ ...base, state: 'running' })).toBe('Running');
		expect(
			describeJob({
				...base,
				state: 'running',
				progress: { current: 3, total: 10, message: 'ocr gate' }
			})
		).toBe('Running · 3/10 · ocr gate');
		expect(describeJob({ ...base, state: 'running', cancel_requested: true })).toBe('Stopping…');
		expect(describeJob({ ...base, state: 'succeeded', result: { outcome: 'written' } })).toBe(
			'Done'
		);
		expect(
			describeJob({ ...base, state: 'succeeded', result: { skipped: 'superseded by job 9' } })
		).toBe('Done · skipped: superseded by job 9');
		expect(describeJob({ ...base, state: 'succeeded', result: { skipped: false } })).toBe('Done');
		expect(describeJob({ ...base, state: 'failed', error: 'timed out after 600 s' })).toBe(
			'Failed · timed out after 600 s'
		);
		expect(describeJob({ ...base, state: 'cancelled' })).toBe('Cancelled');
	});

	it('leave the error out of the detail headline, which shows it below', () => {
		expect(detailHeadline({ ...base, state: 'failed', error: 'timed out' })).toBe('Failed');
		expect(detailHeadline({ ...base, state: 'queued' })).toBe('Queued');
	});

	it('name the kinds', () => {
		expect(kindLabel('library_sync')).toBe('Library sync');
		expect(kindLabel('poster_restore')).toBe('Restore');
		expect(kindLabel('something_new')).toBe('something new');
	});

	it('names the movie a job is about', () => {
		expect(subjectLabel({ movie_id: 1, title: 'Heat', year: 1995 })).toBe('Heat (1995)');
		expect(subjectLabel({ movie_id: 2, title: 'Untitled', year: null })).toBe('Untitled');
		expect(subjectLabel(null)).toBeNull();
		expect(subjectLabel(undefined)).toBeNull();
	});
});
