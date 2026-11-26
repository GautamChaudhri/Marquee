/** Addresses of poster images. A thumbnail carries the poster's version, so the browser may
 *  keep it for a year: a new poster has a new version and so a new address. */
import type { Deployment, Poster } from '#lib/api/types.ts';

export type ThumbnailWidth = 160 | 320 | 640;

export function thumbnailUrl(
	poster: Pick<Poster, 'url' | 'version'>,
	width: ThumbnailWidth
): string {
	const params = new URLSearchParams({ width: String(width), v: poster.version });
	return `${poster.url}?${params}`;
}

/** Who chose the poster, as the page says it. A poster Marquee wrote is the owner's choice
 *  when the newest write was theirs (a poster picked in a review, or put back), and Marquee's
 *  pick when the deploy policy wrote it; without the newest write's `initiated_by` the page
 *  only says that Marquee wrote it. */
export function originLabel(
	origin: Poster['origin'] | null | undefined,
	chosenBy?: Deployment['initiated_by'] | null
): string {
	if (origin === 'marquee') {
		if (chosenBy === 'owner') return 'Your choice';
		if (chosenBy === 'policy') return 'Marquee’s pick';
		return 'Written by Marquee';
	}
	if (origin === 'library') return 'Your poster';
	return 'No poster';
}
