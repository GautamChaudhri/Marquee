import type { PageLoad } from './$types';
import { getLearnedHeads, getTasteMap, getTasteProfiles, getTasteStatus } from '$lib/api/taste';
import type { ManagedArtifactSummary, TasteMapData, TasteStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, url }) => {
	const library = (url.searchParams.get('library') as 'movies' | 'tv') || 'movies';
	try {
		const [status, mapData, profiles, heads] = await Promise.all([
			getTasteStatus(fetch, library),
			getTasteMap(fetch, library).catch(() => null as TasteMapData | null),
			getTasteProfiles(fetch, library)
				.then((value) => value.profiles)
				.catch(() => [] as ManagedArtifactSummary[]),
			getLearnedHeads(fetch, library)
				.then((value) => value.heads)
				.catch(() => [] as ManagedArtifactSummary[])
		]);
		return { status, mapData, profiles, heads, library, error: null as string | null };
	} catch (e) {
		return {
			status: null as TasteStatus | null,
			mapData: null as TasteMapData | null,
			profiles: [] as ManagedArtifactSummary[],
			heads: [] as ManagedArtifactSummary[],
			library,
			error: e instanceof Error ? e.message : 'Failed to load taste status'
		};
	}
};
