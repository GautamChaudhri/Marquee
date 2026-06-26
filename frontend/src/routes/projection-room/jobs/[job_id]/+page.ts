import type { PageLoad } from './$types';
import { getJobDetail } from '$lib/api/jobs';

export const load: PageLoad = async ({ fetch, params }) => {
	try {
		const job = await getJobDetail(fetch, params.job_id);
		return { job, error: null };
	} catch (e) {
		return { job: null, error: e instanceof Error ? e.message : 'Failed to load job' };
	}
};
