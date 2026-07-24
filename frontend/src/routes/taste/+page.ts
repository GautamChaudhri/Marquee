import type { PageLoad } from './$types';
import { getRankingResiduals, getTasteProfiles, getTasteStatus } from '$lib/api/taste';
import type { ManagedArtifactSummary, TasteStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, url }) => {
	const library = (url.searchParams.get('library') as 'movies' | 'tv') || 'movies';
	try {
		const [status, profiles, residuals] = await Promise.all([
			getTasteStatus(fetch, library),
			getTasteProfiles(fetch, library)
				.then((value) => value.profiles)
				.catch(() => [] as ManagedArtifactSummary[]),
			getRankingResiduals(fetch, library)
				.then((value) => value.residuals)
				.catch(() => [] as ManagedArtifactSummary[])
		]);
		return { status, mapData: null, profiles, residuals, library, error: null as string | null };
	} catch (e) {
		return {
			status: null as TasteStatus | null,
			mapData: null,
			profiles: [] as ManagedArtifactSummary[],
			residuals: [] as ManagedArtifactSummary[],
			library,
			error: e instanceof Error ? e.message : 'Failed to load taste status'
		};
	}
};
