import { describe, expect, it } from 'vitest';
import type { PendingReview } from '#lib/api/types.ts';
import {
	canUndo,
	eventLine,
	folderLine,
	movieLabel,
	nextRun,
	rankConfirmation,
	recordedLine,
	rejectConfirmation,
	replaceConfirmation
} from './review';

const base = { chosen_source_file: 'a.jpg', revokes_event_id: null, revoked_by: null };

describe('events', () => {
	it('says what each act did', () => {
		expect(eventLine({ ...base, action: 'approval', context: {} })).toBe(
			'Kept the top pick (a.jpg)'
		);
		expect(eventLine({ ...base, action: 'override', context: {} })).toBe('Chose a.jpg');
		expect(
			eventLine({ ...base, action: 'rank', context: { order: ['a.jpg', 'b.jpg'], hated: ['c'] } })
		).toBe('Ranked 2 designs with a.jpg first; 1 hated design');
		expect(
			eventLine({ ...base, action: 'reject', chosen_source_file: null, context: { hated: [] } })
		).toBe('Refused every design; 0 hated designs');
		expect(eventLine({ ...base, action: 'revoke', revokes_event_id: 7, context: {} })).toBe(
			'Undid answer 7'
		);
	});

	it('undoes only an answer that stands', () => {
		expect(canUndo({ action: 'approval', revoked_by: null })).toBe(true);
		expect(canUndo({ action: 'approval', revoked_by: 9 })).toBe(false);
		expect(canUndo({ action: 'revoke', revoked_by: null })).toBe(false);
	});

	it('shows the event the API returned, and a key used before', () => {
		const event = { ...base, id: 12, action: 'approval' as const, context: {} };
		expect(recordedLine(event, 201, 'approval')).toEqual([
			'Recorded as answer 12: Kept the top pick (a.jpg).'
		]);
		expect(recordedLine(event, 201, 'override')).toContain(
			'The poster you chose is the top pick, so it was recorded as keeping the top pick.'
		);
		expect(recordedLine(event, 200, 'approval')).toContain(
			'This answer had already been recorded; nothing changed.'
		);
	});
});

describe('the folder', () => {
	it('says what happens to the movie’s folder', () => {
		expect(folderLine('reject', true, null, null)).toBe(
			'Nothing is written to the movie’s folder.'
		);
		expect(folderLine('rank', true, 41, null)).toBe(
			'Deploy job 41 writes the chosen poster to the movie’s folder.'
		);
		expect(folderLine('override', true, null, 'the movie has no folder')).toBe(
			'No poster is written: the movie has no folder'
		);
		expect(folderLine('approval', false, null, null)).toMatch(/you chose not to/);
	});
});

describe('confirmations', () => {
	it('says which poster is written and what the hate pile becomes', () => {
		expect(rankConfirmation('Design 3', 2, true)).toBe(
			'Design 3 is written to the movie’s folder; what is there now is backed up first. The 2 hated designs become examples of what you do not want (those ranked 10 or better, at most 8 per movie).'
		);
		expect(rankConfirmation('Design 3', 0, false)).toMatch(
			/nothing is written.*No design is hated/
		);
		expect(rejectConfirmation(5)).toMatch(/^Nothing is written.*The 5 designs shown become/);
	});

	it('asks before replacing the owner’s own poster, and only then', () => {
		expect(replaceConfirmation('the top pick', true, 'library')).toMatch(/over your own poster/);
		expect(replaceConfirmation('the top pick', false, 'library')).toBeNull();
		expect(replaceConfirmation('the top pick', true, 'marquee')).toBeNull();
		expect(replaceConfirmation('the top pick', true, null)).toBeNull();
	});
});

describe('what comes next', () => {
	it('is the next run waiting, other than this one', () => {
		const item = (run_id: number) => ({ run_id }) as PendingReview;
		expect(nextRun([item(3), item(4)], 3)?.run_id).toBe(4);
		expect(nextRun([item(3)], 3)).toBeNull();
		expect(nextRun([], 3)).toBeNull();
		expect(movieLabel({ title: 'Heat', year: 1995 })).toBe('Heat (1995)');
		expect(movieLabel({ title: 'Heat', year: null })).toBe('Heat');
	});
});
