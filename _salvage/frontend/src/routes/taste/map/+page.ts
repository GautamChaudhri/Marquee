import type { PageLoad } from './$types';

export type MapView = 'films' | 'shows' | 'seasons';

/** The projection is a large per-exemplar payload and 404s whenever a namespace has
 *  no published map, so it is fetched client-side rather than serialised through SSR. */
export const load: PageLoad = ({ url }) => {
	const library = url.searchParams.get('library') === 'tv' ? 'tv' : 'movies';
	const requested = url.searchParams.get('view');
	const view: MapView =
		requested === 'films' || requested === 'shows' || requested === 'seasons'
			? requested
			: library === 'tv'
				? 'shows'
				: 'films';
	return { library, view };
};
