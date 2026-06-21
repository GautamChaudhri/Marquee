import type { PageLoad } from './$types';
import { getRunResults } from '$lib/api/pipeline';
import type { RunResultsResponse } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, params }) => {
	try {
		const run = await getRunResults(fetch, params.run_id);
		return { run, runId: params.run_id, error: null as string | null };
	} catch (e) {
		return {
			run: null as RunResultsResponse | null,
			runId: params.run_id,
			error: e instanceof Error ? e.message : 'Failed to load run'
		};
	}
};
