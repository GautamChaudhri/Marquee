import { getHdrSummary } from '$lib/api/hdr';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
	try {
		const summary = await getHdrSummary(fetch);
		return { summary, error: null as string | null };
	} catch (e) {
		return {
			summary: null,
			error: e instanceof Error ? e.message : 'Failed to load HDR summary'
		};
	}
};
