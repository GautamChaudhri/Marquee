import type { PageLoad } from './$types';
import { getPresentation } from '$lib/activity/client';

export const load: PageLoad = async ({ fetch, params }) => {
	try {
		const presentation = await getPresentation(fetch, params.job_id);
		return { presentation, error: null };
	} catch (e) {
		return {
			presentation: null,
			error: e instanceof Error ? e.message : 'Failed to load job presentation'
		};
	}
};
