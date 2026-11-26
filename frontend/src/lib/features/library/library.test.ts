import { describe, expect, it } from 'vitest';
import {
	DEFAULT_FILTERS,
	PAGE_SIZE,
	filtersFromParams,
	isFiltered,
	movieQuery,
	paramsFromFilters
} from './filters';
import { originLabel, thumbnailUrl } from './poster';

describe('library filters', () => {
	it('read the address, ignoring what is not allowed', () => {
		const params = new URLSearchParams('q=alien&poster=no&origin=marquee&run=yes&sort=year');
		expect(filtersFromParams(params)).toEqual({
			q: 'alien',
			poster: 'no',
			origin: 'marquee',
			run: 'yes',
			sort: 'year'
		});
		const odd = new URLSearchParams('poster=maybe&origin=tmdb&run=1&sort=rating');
		expect(filtersFromParams(odd)).toEqual(DEFAULT_FILTERS);
		expect(filtersFromParams(new URLSearchParams(`q=${'x'.repeat(300)}`)).q).toHaveLength(200);
	});

	it('write only what differs from the defaults', () => {
		expect(paramsFromFilters(DEFAULT_FILTERS).toString()).toBe('');
		const changed = {
			...DEFAULT_FILTERS,
			q: '  heat ',
			origin: 'library' as const,
			sort: 'added' as const
		};
		expect(paramsFromFilters(changed).toString()).toBe('q=heat&origin=library&sort=added');
		expect(filtersFromParams(paramsFromFilters(changed))).toEqual({ ...changed, q: 'heat' });
	});

	it('say whether anything narrows the list (the sort does not)', () => {
		expect(isFiltered(DEFAULT_FILTERS)).toBe(false);
		expect(isFiltered({ ...DEFAULT_FILTERS, sort: 'year' })).toBe(false);
		expect(isFiltered({ ...DEFAULT_FILTERS, run: 'no' })).toBe(true);
		expect(isFiltered({ ...DEFAULT_FILTERS, q: '   ' })).toBe(false);
	});

	it('become the query GET /api/movies takes', () => {
		expect(movieQuery(DEFAULT_FILTERS, 1)).toEqual({
			page: 1,
			page_size: PAGE_SIZE,
			sort: 'title'
		});
		expect(
			movieQuery({ q: 'up', poster: 'yes', origin: 'marquee', run: 'no', sort: 'year' }, 3, 10)
		).toEqual({
			page: 3,
			page_size: 10,
			sort: 'year',
			q: 'up',
			has_poster: true,
			origin: 'marquee',
			has_run: false
		});
	});
});

describe('poster addresses', () => {
	it('carry the width and the version', () => {
		const poster = { url: '/api/movies/7/poster', version: 'abcdef012345' };
		expect(thumbnailUrl(poster, 320)).toBe('/api/movies/7/poster?width=320&v=abcdef012345');
		expect(thumbnailUrl(poster, 640)).toContain('width=640');
	});

	it('say whose poster it is', () => {
		expect(originLabel('library')).toBe('Your poster');
		expect(originLabel('marquee', 'policy')).toBe('Marquee’s pick');
		expect(originLabel('marquee', 'owner')).toBe('Your choice');
		expect(originLabel('marquee')).toBe('Written by Marquee');
		expect(originLabel('marquee', null)).toBe('Written by Marquee');
		expect(originLabel('library', 'owner')).toBe('Your poster');
		expect(originLabel(null)).toBe('No poster');
	});
});
