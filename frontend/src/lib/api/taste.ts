import { apiGet, apiSend, type Fetch } from './client';
import type { JobSummary, TasteSource, TasteStatus } from './types';

/** Taste-profile + learned-head ("Key Art Engine") status, labels, exemplars. */
export function getTasteStatus(fetchFn: Fetch): Promise<TasteStatus> {
	return apiGet<TasteStatus>(fetchFn, '/taste/status');
}

/** Rebuild the taste profile (initial training). `training_dir` (default) uses
 *  the curated folder; `library` rebuilds from every deployed poster. */
export function retrainTaste(
	fetchFn: Fetch,
	source: TasteSource = 'training_dir'
): Promise<JobSummary> {
	return apiSend<JobSummary>(fetchFn, 'POST', '/taste/retrain', { source });
}

/** Train the learned head (UI "Key Art Engine") from accumulated labels. */
export function retrainHead(fetchFn: Fetch): Promise<JobSummary> {
	return apiSend<JobSummary>(fetchFn, 'POST', '/taste/head/retrain');
}

/** Request cancellation of a running taste-profile rebuild. */
export function cancelRetrain(
	fetchFn: Fetch
): Promise<{ status: string } & Record<string, unknown>> {
	return apiSend(fetchFn, 'POST', '/taste/retrain/cancel', {});
}

// ── Taste map ──────────────────────────────────────────────────────────────
import type {
	TasteMapCandidateOverlay,
	TasteMapData,
	TasteNeighbor
} from './types';

/** Load (or force-rebuild) the 3D/2D taste-map projection. */
export function getTasteMap(
	fetchFn: Fetch,
	recompute = false
): Promise<TasteMapData> {
	const params = recompute ? '?recompute=true' : '';
	return apiGet<TasteMapData>(fetchFn, `/taste/map${params}`);
}

/** Project a pipeline run's ranked candidates into the taste-map space. */
export function overlayCandidates(
	fetchFn: Fetch,
	runId: string
): Promise<TasteMapCandidateOverlay> {
	return apiSend<TasteMapCandidateOverlay>(fetchFn, 'POST', '/taste/map/candidates', {
		run_id: runId
	});
}

/** Run profile enrichment (genres, years, tmdb_ids) and rebuild the map.
 *  Returns the refreshed taste-map data directly. */
export function enrichProfile(fetchFn: Fetch): Promise<TasteMapData> {
	return apiSend<TasteMapData>(fetchFn, 'POST', '/taste/enrich');
}

/** The k nearest exemplars to a given exemplar (click-to-explore). */
export function getExemplarNeighbors(
	fetchFn: Fetch,
	name: string
): Promise<{ name: string; neighbors: TasteNeighbor[] }> {
	return apiGet<{ name: string; neighbors: TasteNeighbor[] }>(
		fetchFn,
		`/taste/exemplars/${encodeURIComponent(name)}/neighbors`
	);
}
