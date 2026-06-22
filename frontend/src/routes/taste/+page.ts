import type { PageLoad } from './$types';
import { getTasteStatus, getTasteMap } from '$lib/api/taste';
import type { TasteMapData, TasteStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	try {
		const [status, mapData] = await Promise.all([
			getTasteStatus(fetch),
			getTasteMap(fetch).catch(() => null as TasteMapData | null)
		]);
		return { status, mapData, error: null as string | null };
	} catch (e) {
		return {
			status: null as TasteStatus | null,
			mapData: null as TasteMapData | null,
			error: e instanceof Error ? e.message : 'Failed to load taste status'
		};
	}
};
