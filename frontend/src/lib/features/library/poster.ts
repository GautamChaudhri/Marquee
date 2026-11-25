/** Addresses of poster images. A thumbnail carries the poster's version, so the browser may
 *  keep it for a year: a new poster has a new version and so a new address. */
import type { Poster } from '#lib/api/types.ts';

export type ThumbnailWidth = 160 | 320 | 640;

export function thumbnailUrl(
	poster: Pick<Poster, 'url' | 'version'>,
	width: ThumbnailWidth
): string {
	const params = new URLSearchParams({ width: String(width), v: poster.version });
	return `${poster.url}?${params}`;
}

/** Who put the poster there, as the page says it. */
export function originLabel(origin: Poster['origin'] | null | undefined): string {
	if (origin === 'marquee') return 'Marquee’s pick';
	if (origin === 'library') return 'Your poster';
	return 'No poster';
}
