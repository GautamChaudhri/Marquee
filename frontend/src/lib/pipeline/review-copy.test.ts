import { describe, expect, it } from 'vitest';
import { rejectAllCopy } from './review-copy';

describe('rejectAllCopy', () => {
	it('keeps the movie confirmation copy for movie runs', () => {
		expect(rejectAllCopy({ mediaType: 'movie' })).toEqual({
			title: 'Reject all candidates',
			message:
				'Records a negative label for the auto-pick and leaves this movie without a chosen poster. You can re-run later.'
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
