import type { PageLoad } from './$types';
import { getLearnedHeads, getTasteMap, getTasteProfiles, getTasteStatus } from '$lib/api/taste';
import type { ManagedArtifactSummary, TasteMapData, TasteStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	try {
		const [status, mapData, profiles, heads] = await Promise.all([
			getTasteStatus(fetch),
			getTasteMap(fetch).catch(() => null as TasteMapData | null),
			getTasteProfiles(fetch).then((value) => value.profiles).catch(() => [] as ManagedArtifactSummary[]),
			getLearnedHeads(fetch).then((value) => value.heads).catch(() => [] as ManagedArtifactSummary[])
		]);
		return { status, mapData, profiles, heads, error: null as string | null };
	} catch (e) {
		return {
			status: null as TasteStatus | null,
			mapData: null as TasteMapData | null,
			profiles: [] as ManagedArtifactSummary[],
			heads: [] as ManagedArtifactSummary[],
			error: e instanceof Error ? e.message : 'Failed to load taste status'
		};
	}
};
