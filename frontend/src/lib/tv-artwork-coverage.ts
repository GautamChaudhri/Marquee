import type { SeriesListItem } from '$lib/api/types';

export type ArtworkCoverageState = 'deployed' | 'partial' | 'missing';

export type ArtworkCoverageTone = 'good' | 'warn' | 'bad';

export interface ArtworkCoverage {
	state: ArtworkCoverageState;
	tone: ArtworkCoverageTone;
	label: 'Deployed' | 'Partially deployed' | 'Missing';
	accessibleLabel: string;
	presentPosterCount: number;
	totalPosterCount: number;
}

/**
 * Collapse the show and exact downloaded-season list into one status. The
 * season array is authoritative; aggregate counters are intentionally ignored
 * so stale sync metadata cannot paint a false green state.
 */
export function deriveArtworkCoverage(item: SeriesListItem): ArtworkCoverage {
	const seasons = Array.isArray(item.seasons) ? item.seasons : [];
	const totalPosterCount = seasons.length + 1;
	const presentPosterCount =
		(item.poster.has_poster ? 1 : 0) +
		seasons.reduce((count, season) => count + (season.poster.has_poster ? 1 : 0), 0);

	if (presentPosterCount === totalPosterCount) {
		return {
			state: 'deployed',
			tone: 'good',
			label: 'Deployed',
			accessibleLabel:
				totalPosterCount === 1
					? 'Deployed: poster present'
					: `Deployed: all ${totalPosterCount} posters present`,
			presentPosterCount,
			totalPosterCount
		};
	}

	if (presentPosterCount > 0) {
		return {
			state: 'partial',
			tone: 'warn',
			label: 'Partially deployed',
			accessibleLabel: `Partially deployed: ${presentPosterCount} of ${totalPosterCount} posters present`,
			presentPosterCount,
			totalPosterCount
		};
	}

	return {
		state: 'missing',
		tone: 'bad',
		label: 'Missing',
		accessibleLabel:
			totalPosterCount === 1
				? 'Missing: poster not present'
				: `Missing: none of ${totalPosterCount} posters present`,
		presentPosterCount,
		totalPosterCount
	};
}
