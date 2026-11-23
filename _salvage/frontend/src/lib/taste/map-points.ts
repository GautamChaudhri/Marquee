/** Shared reading of a taste-map point, used by the plot and by the detail panel. */
import type { TasteMapAssetKind, TasteMapPoint } from '$lib/api/types';

/** Whether the projection is read flat or orbited. */
export type ViewMode = '2d' | '3d';
/** What the marker colour encodes. */
export type ColorMode = 'cluster' | 'genre' | 'aesthetic' | 'colorfulness' | 'year' | 'self_knn';

const KIND_LABELS: Record<TasteMapAssetKind, string> = {
	movie: 'Film',
	show: 'Show',
	season: 'Season'
};

/** "Andor · Season 2" — the subject, plus the season when the point is one. */
export function pointTitle(point: TasteMapPoint): string {
	const base = point.movie_title || point.name.replace(/\.(jpg|jpeg|png|webp)$/i, '');
	return point.season_number != null ? `${base} · Season ${point.season_number}` : base;
}

export function kindLabel(kind: TasteMapAssetKind): string {
	return KIND_LABELS[kind] ?? 'Poster';
}

/** Where this point's subject lives in the library, when it resolved to one. */
export function subjectHref(point: TasteMapPoint): string | null {
	if (point.asset_kind === 'movie' && point.movie_id != null) return `/films/${point.movie_id}`;
	if (point.series_id != null) return `/shows/${point.series_id}`;
	return null;
}
