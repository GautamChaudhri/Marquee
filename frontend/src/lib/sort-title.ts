/**
 * Sort-title utility: strips leading articles so "The Dark Knight" sorts
 * under D and "A Beautiful Mind" under B — matching Plex/Jellyfin/IMDb
 * convention.
 *
 * Import `sortTitle` wherever you need article-aware sorting.
 * Extend `SORT_TITLE_ARTICLES` to add more languages.
 */

/** Leading articles stripped for sorting purposes. Ordered longest-first so
 *  multi-word articles ("Los") match before their prefix ("Lo"). */
export const SORT_TITLE_ARTICLES: readonly string[] = [
	'The', 'Les', 'Los', 'Das', 'Gli',  // 3-4 char
	'Die', 'Der', 'Las', 'Le', 'As',    // 3 char
	'An', 'El', 'Il', 'Lo', 'Os',       // 2 char
	'A', 'O',                             // 1 char
];

const _lowered = SORT_TITLE_ARTICLES.map((a) => a.toLowerCase());

/**
 * Return a sort-friendly form of `title` with the leading article moved to
 * the end: `"The Dark Knight"` → `"Dark Knight, The"`.
 *
 * Use the result ONLY for sorting — never display it.
 */
export function sortTitle(title: string): string {
	const lowered = title.toLowerCase();
	for (let i = 0; i < SORT_TITLE_ARTICLES.length; i++) {
		const prefixLower = _lowered[i] + ' ';
		if (lowered.startsWith(prefixLower)) {
			const article = SORT_TITLE_ARTICLES[i];
			return title.slice(article.length + 1) + ', ' + article;
		}
	}
	return title;
}

/**
 * Comparator for `Array.sort()` that sorts by title ignoring leading articles.
 *
 * Usage:
 *   items.sort(compareBySortTitle);
 *   // or with a selector:
 *   items.sort((a, b) => compareBySortTitle(a.title, b.title));
 */
export function compareBySortTitle(a: string, b: string): number {
	return sortTitle(a).localeCompare(sortTitle(b));
}
