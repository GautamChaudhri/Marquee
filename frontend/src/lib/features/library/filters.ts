/** The Library page's search, filters, and sort, kept in the page's address so a view can be
 *  linked and survives a reload, and turned into the query `GET /api/movies` takes. */

export type Sort = 'title' | 'year' | 'added';
export type Tri = 'any' | 'yes' | 'no';
export type Origin = 'any' | 'library' | 'marquee';

export interface LibraryFilters {
	q: string;
	poster: Tri;
	origin: Origin;
	run: Tri;
	sort: Sort;
}

export const DEFAULT_FILTERS: LibraryFilters = {
	q: '',
	poster: 'any',
	origin: 'any',
	run: 'any',
	sort: 'title'
};

export const PAGE_SIZE = 48;

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
	return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

export function filtersFromParams(params: Pick<URLSearchParams, 'get'>): LibraryFilters {
	return {
		q: (params.get('q') ?? '').slice(0, 200),
		poster: oneOf(params.get('poster'), ['any', 'yes', 'no'], 'any'),
		origin: oneOf(params.get('origin'), ['any', 'library', 'marquee'], 'any'),
		run: oneOf(params.get('run'), ['any', 'yes', 'no'], 'any'),
		sort: oneOf(params.get('sort'), ['title', 'year', 'added'], 'title')
	};
}

/** Only what differs from the defaults, so the plain library has a plain address. */
export function paramsFromFilters(filters: LibraryFilters): URLSearchParams {
	const params = new URLSearchParams();
	const q = filters.q.trim();
	if (q) params.set('q', q);
	for (const name of ['poster', 'origin', 'run', 'sort'] as const) {
		if (filters[name] !== DEFAULT_FILTERS[name]) params.set(name, filters[name]);
	}
	return params;
}

export function isFiltered(filters: LibraryFilters): boolean {
	return paramsFromFilters({ ...filters, sort: DEFAULT_FILTERS.sort }).size > 0;
}

function tri(value: Tri): boolean | undefined {
	return value === 'any' ? undefined : value === 'yes';
}

/** The query for one page of `GET /api/movies`. */
export function movieQuery(filters: LibraryFilters, page: number, pageSize = PAGE_SIZE) {
	const q = filters.q.trim();
	return {
		page,
		page_size: pageSize,
		sort: filters.sort,
		...(q ? { q } : {}),
		...(tri(filters.poster) !== undefined ? { has_poster: tri(filters.poster) } : {}),
		...(filters.origin !== 'any' ? { origin: filters.origin } : {}),
		...(tri(filters.run) !== undefined ? { has_run: tri(filters.run) } : {})
	};
}
