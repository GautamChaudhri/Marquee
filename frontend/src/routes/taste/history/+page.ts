import type { PageLoad } from './$types';
import { getRankingResiduals, getTasteProfiles } from '$lib/api/taste';
import type { ManagedArtifactSummary } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, url }) => {
	const library = (url.searchParams.get('library') as 'movies' | 'tv') || 'movies';
	const [profiles, residuals] = await Promise.all([
		getTasteProfiles(fetch, library)
			.then((value) => value.profiles)
			.catch(() => [] as ManagedArtifactSummary[]),
		getRankingResiduals(fetch, library)
			.then((value) => value.residuals)
			.catch(() => [] as ManagedArtifactSummary[])
	]);
	return { profiles, residuals, library };
};
