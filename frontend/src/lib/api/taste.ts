import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import type {
	ManagedHeadsResponse,
	ManagedExemplarRow,
	ManagedHeadDetail,
	ManagedProfilesResponse,
	ManagedProfileDetail,
	TasteMapData,
	TasteNeighbor,
	TasteSource,
	TasteStatus
} from './types';

type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];

export type TasteLibrary = 'movies' | 'tv';

/** Taste-profile + learned-head ("Key Art Engine") status, labels, exemplars. */
export function getTasteStatus(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<TasteStatus> {
	return apiGet<TasteStatus>(fetchFn, '/taste/status', { library });
}

/** Rebuild the taste profile (initial training). `training_dir` (default) uses
 *  the curated folder; `library` rebuilds from every deployed poster. */
export function retrainTaste(
	fetchFn: Fetch,
	source: TasteSource = 'training_dir',
	library: TasteLibrary = 'movies'
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/taste/retrain', { source, library });
}

/** Train the learned head (UI "Key Art Engine") from accumulated labels. */
export function retrainHead(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<JobSubmissionResponse> {
	if (library === 'movies')
		return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/taste/head/retrain');
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/taste/head/retrain?library=${library}`);
}

/** Request cancellation of a running taste-profile rebuild. */
export function cancelRetrain(
	fetchFn: Fetch
): Promise<{ status: string } & Record<string, unknown>> {
	return apiSend(fetchFn, 'POST', '/taste/retrain/cancel', {});
}

/** Load the last published 3D/2D taste-map projection. */
export function getTasteMap(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<TasteMapData> {
	return apiGet<TasteMapData>(fetchFn, '/taste/map', { library });
}

export function rebuildTasteMap(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/taste/map/rebuild?library=${library}`);
}

/** Publish enriched profile metadata through the canonical job system. */
export function enrichProfile(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<JobSubmissionResponse> {
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', `/taste/enrich?library=${library}`);
}

/** The k nearest exemplars to a given exemplar (click-to-explore). */
export function getExemplarNeighbors(
	fetchFn: Fetch,
	name: string,
	library: TasteLibrary = 'movies'
): Promise<{ name: string; neighbors: TasteNeighbor[] }> {
	return apiGet<{ name: string; neighbors: TasteNeighbor[] }>(
		fetchFn,
		`/taste/exemplars/${encodeURIComponent(name)}/neighbors`,
		{ library }
	);
}

export function getTasteProfiles(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<ManagedProfilesResponse> {
	return apiGet(fetchFn, '/taste/profiles', { library });
}

export function getTasteProfileDetail(
	fetchFn: Fetch,
	artifactId: string,
	library: TasteLibrary = 'movies'
): Promise<ManagedProfileDetail> {
	return apiGet(fetchFn, `/taste/profiles/${artifactId}`, { library });
}

export function getTasteProfileExemplars(
	fetchFn: Fetch,
	artifactId: string,
	library: TasteLibrary = 'movies'
): Promise<{ exemplars: ManagedExemplarRow[] }> {
	return apiGet(fetchFn, `/taste/profiles/${artifactId}/exemplars`, { library });
}

export function getLearnedHeads(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<ManagedHeadsResponse> {
	return apiGet(fetchFn, '/taste/heads', { library });
}

export function getLearnedHeadDetail(
	fetchFn: Fetch,
	artifactId: string,
	library: TasteLibrary = 'movies'
): Promise<ManagedHeadDetail> {
	return apiGet(fetchFn, `/taste/heads/${artifactId}`, { library });
}
