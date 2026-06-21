import type { PageLoad } from './$types';
import { getTasteStatus } from '$lib/api/taste';
import type { TasteStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	try {
		return { status: await getTasteStatus(fetch), error: null as string | null };
	} catch (e) {
		return {
			status: null as TasteStatus | null,
			error: e instanceof Error ? e.message : 'Failed to load taste status'
		};
	}
};
