import { describe, expect, it } from 'vitest';
import {
	rejectAllCopy,
	relativeAge,
	reviewCandidateSummary,
	reviewModeCopy,
	reviewResultsNotice
} from './review-copy';

describe('rejectAllCopy', () => {
	it('keeps the film confirmation copy for movie runs', () => {
		expect(rejectAllCopy({ mediaType: 'movie' })).toEqual({
			title: 'Reject all candidates',
			message:
				'Records a negative label for the auto-pick and leaves this film without a chosen poster. You can re-run later.'
		});
	});

	it('names the show poster for TV show runs', () => {
		expect(rejectAllCopy({ mediaType: 'series' })).toEqual({
			title: 'Reject all show poster candidates',
			message:
				'Records a negative label for the auto-pick and leaves this TV show without a chosen show poster. You can re-run later.'
		});
	});

	it('names the season poster and season number for TV season runs', () => {
		expect(rejectAllCopy({ mediaType: 'season', seasonNumber: 2 })).toEqual({
			title: 'Reject all season poster candidates',
			message:
				'Records a negative label for the auto-pick and leaves Season 2 of this TV show without a chosen season poster. You can re-run later.'
		});
	});
});

describe('reviewModeCopy', () => {
	it('describes cold-start candidates as manual review, not a ranking', () => {
		expect(reviewModeCopy('collecting', 'movie')).toEqual({
			tabLabel: 'Review candidates',
			guidance:
				'These candidates passed objective checks. Their display order is neutral, not a recommendation. Choose one to teach Movies taste.'
		});
	});

	it('keeps the Television taste namespace explicit for TV runs', () => {
		expect(reviewModeCopy('collecting', 'series').guidance).toContain('Television taste');
	});

	it('keeps the ranked presentation for profile-backed runs', () => {
		expect(reviewModeCopy('personalized')).toEqual({ tabLabel: 'Ranked', guidance: null });
	});
});

describe('reviewCandidateSummary', () => {
	it('marks a persisted personalized auto-pick as successful', () => {
		expect(reviewCandidateSummary(36, true)).toEqual({ label: '36 candidates', tone: 'good' });
	});

	it('keeps neutral cold-start candidates in the manual-review state', () => {
		expect(reviewCandidateSummary(3, false)).toEqual({ label: '3 candidates', tone: 'warn' });
	});

	it('surfaces a run with no candidates as an error', () => {
		expect(reviewCandidateSummary(0, true)).toEqual({ label: '0 candidates', tone: 'bad' });
	});
});

describe('reviewResultsNotice', () => {
	it('keeps survivor-bearing cold starts in one manual-review state', () => {
		expect(reviewResultsNotice('collecting', 3, 'movie')).toEqual({
			tone: 'warn',
			title: 'Manual review',
			message:
				'These candidates passed objective checks. Their display order is neutral, not a recommendation. Choose one to teach Movies taste.'
		});
	});

	it('makes zero survivors an error regardless of review mode', () => {
		const expected = {
			tone: 'bad',
			title: 'No survivors',
			message: 'No candidates survived the objective checks. Re-run this title to try again.'
		};
		expect(reviewResultsNotice('collecting', 0, 'series')).toEqual(expected);
		expect(reviewResultsNotice('personalized', 0, 'movie')).toEqual(expected);
	});

	it('does not add manual-review guidance to personalized survivors', () => {
		expect(reviewResultsNotice('personalized', 3, 'movie')).toBeNull();
	});
});

describe('relativeAge', () => {
	it('uses the same compact display for both review workspaces', () => {
		const now = Date.parse('2026-08-01T12:00:00Z');
		expect(relativeAge('2026-08-01T11:58:00Z', now)).toBe('2m ago');
		expect(relativeAge('2026-07-31T10:00:00Z', now)).toBe('1d ago');
		expect(relativeAge(null, now)).toBe('—');
	});
});
