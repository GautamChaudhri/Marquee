/** Pure poster-URL builders. Kept free of `$env` imports so poster-rendering
 *  components (and their unit tests) can use them without the API client's
 *  environment wiring. */

export function getSeriesPosterUrl(id: number, version?: string | null): string {
	return `/api/library/series/${id}/poster${version ? `?v=${version}` : ''}`;
}

export function getSeasonPosterUrl(id: number, version?: string | null): string {
	return `/api/library/seasons/${id}/poster${version ? `?v=${version}` : ''}`;
}

/** Grid-width derivative of a library poster URL (~20-40 KB instead of the
 *  full TMDB original). Non-library URLs (run candidates, already small) pass
 *  through untouched, so this is safe to apply to any poster source. Keep the
 *  original URL for the fullscreen viewer. */
export function posterThumbUrl(url: string | null): string | null {
	if (!url || !url.startsWith('/api/library/')) return url;
	return `${url}${url.includes('?') ? '&' : '?'}w=400`;
}
