import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';
import type {
	ManagedResidualsResponse,
	ManagedExemplarRow,
	ManagedResidualDetail,
	ManagedProfilesResponse,
	ManagedProfileDetail,
	TasteMapData,
	TasteNeighbor,
	TasteStatus
} from './types';

type JobSubmissionResponse = components['schemas']['JobSubmissionResponse'];
type TasteRetrainRequest = components['schemas']['TasteRetrainRequest'];

export type TasteLibrary = 'movies' | 'tv';

/** Taste-profile + bounded-residual status, labels, and exemplars. */
export function getTasteStatus(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<TasteStatus> {
	return apiGet<TasteStatus>(fetchFn, '/taste/status', { library });
}

/** Request a taste-profile rebuild from canonical approved evidence. */
export function retrainTaste(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<JobSubmissionResponse> {
	const request = { source: 'canonical_revision', library } satisfies TasteRetrainRequest;
	return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/taste/retrain', request);
}

/** Train the bounded residual from canonical preference evidence. */
export function retrainResidual(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<JobSubmissionResponse> {
	if (library === 'movies')
		return apiSend<JobSubmissionResponse>(fetchFn, 'POST', '/taste/residual/retrain');
	return apiSend<JobSubmissionResponse>(
		fetchFn,
		'POST',
		`/taste/residual/retrain?library=${library}`
	);
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

export function getRankingResiduals(
	fetchFn: Fetch,
	library: TasteLibrary = 'movies'
): Promise<ManagedResidualsResponse> {
	return apiGet(fetchFn, '/taste/residuals', { library });
}

export function getRankingResidualDetail(
	fetchFn: Fetch,
	artifactId: string,
	library: TasteLibrary = 'movies'
): Promise<ManagedResidualDetail> {
	return apiGet(fetchFn, `/taste/residuals/${artifactId}`, { library });
}
