import { describe, expect, it } from 'vitest';
import {
	assetBreakdown,
	assetNoun,
	countOfSubjects,
	libraryLabel,
	subjectNoun,
	subjectNounTitle
} from './library-copy';

describe('library-copy', () => {
	// The UI always says "film"; only the API and the stored asset kind say "movie".
	it('names a subject after the library it came from', () => {
		expect(subjectNoun('movies', 2)).toBe('films');
		expect(subjectNoun('movies', 1)).toBe('film');
		expect(subjectNoun('tv', 2)).toBe('shows');
		expect(subjectNoun('tv', 1)).toBe('show');
		expect(subjectNounTitle('movies')).toBe('Films');
		expect(subjectNounTitle('tv')).toBe('Shows');
		expect(libraryLabel('tv')).toBe('Television');
		expect(libraryLabel('movies')).toBe('Films');
	});

	it('agrees on number when counting subjects', () => {
		expect(countOfSubjects('tv', 43)).toBe('43 shows');
		expect(countOfSubjects('tv', 1)).toBe('1 show');
		expect(countOfSubjects('movies', 0)).toBe('0 films');
		expect(countOfSubjects('movies', 1)).toBe('1 film');
	});

	it('orders an asset breakdown shows-then-seasons, not alphabetically', () => {
		expect(assetBreakdown({ season: 4, show: 1 })).toBe('1 show · 4 seasons');
		expect(assetBreakdown({ movie: 1 })).toBe('1 film');
		expect(assetBreakdown({ movie: 3 })).toBe('3 films');
	});

	it('drops empty kinds rather than printing a zero', () => {
		expect(assetBreakdown({ show: 1, season: 0 })).toBe('1 show');
		expect(assetBreakdown({})).toBe('');
	});

	it('falls back to a plural s for an unrecognised kind', () => {
		expect(assetNoun('episode', 3)).toBe('episodes');
		expect(assetNoun('unknown', 2)).toBe('posters');
	});
});
