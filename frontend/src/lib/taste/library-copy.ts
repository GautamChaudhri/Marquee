/** Nouns for the Key Art Engine, which serves two libraries out of one page.
 *
 * The taste API calls a subject a "movie" in both namespaces — a legacy of the
 * movies-only original. On the TV tab that is simply wrong, so every user-facing
 * count routes through here instead of hardcoding "movies".
 */

export type TasteLibrary = 'movies' | 'tv';

export function libraryLabel(library: TasteLibrary): string {
	return library === 'tv' ? 'Television' : 'Films';
}

/** What one entry in this library is: a film, or a show. */
export function subjectNoun(library: TasteLibrary, count = 2): string {
	if (library === 'tv') return count === 1 ? 'show' : 'shows';
	return count === 1 ? 'film' : 'films';
}

export function subjectNounTitle(library: TasteLibrary): string {
	return library === 'tv' ? 'Shows' : 'Films';
}

// Keyed by the kind the backend tags an asset with, which is still "movie".
const ASSET_NOUNS: Record<string, [string, string]> = {
	show: ['show', 'shows'],
	season: ['season', 'seasons'],
	movie: ['film', 'films'],
	unknown: ['poster', 'posters']
};

/** What one poster inside a profile is, by the kind the trainer tagged it with. */
export function assetNoun(kind: string, count: number): string {
	const [singular, plural] = ASSET_NOUNS[kind] ?? [kind, `${kind}s`];
	return count === 1 ? singular : plural;
}

// Shows before their seasons, movies after both; anything unrecognised sorts last.
// Mirrors the ordering the backend applies in publication_catalog._asset_breakdown.
const KIND_ORDER: Record<string, number> = { show: 0, season: 1, movie: 2 };

/** "1 show · 4 seasons" — what a subject actually contributed to a profile. */
export function assetBreakdown(byKind: Record<string, number>): string {
	return Object.entries(byKind)
		.filter(([, count]) => count > 0)
		.sort(([a], [b]) => (KIND_ORDER[a] ?? 9) - (KIND_ORDER[b] ?? 9) || a.localeCompare(b))
		.map(([kind, count]) => `${count} ${assetNoun(kind, count)}`)
		.join(' · ');
}

/** "107 shows" / "1 film" — a count and its noun, agreeing on number. */
export function countOfSubjects(library: TasteLibrary, count: number): string {
	return `${count} ${subjectNoun(library, count)}`;
}
