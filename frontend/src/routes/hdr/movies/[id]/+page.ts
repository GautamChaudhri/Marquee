import { getHdrDetail } from '$lib/api/radarr-overlay';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params }) => {
	const id = Number(params.id);
	if (!id) return { detail: null, error: 'Invalid movie ID' };
	try {
		const detail = await getHdrDetail(fetch, id);
		return { detail, error: null as string | null };
	} catch (e) {
		return {
			detail: null,
			error: e instanceof Error ? e.message : 'Failed to load HDR detail'
		};
	}
};
