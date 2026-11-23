import type { PageLoad } from './$types';
import { getPipelineSummary } from '$lib/api/pipeline';
import { getTvSummary } from '$lib/api/pipeline-tv';
import type { PipelineSummary, TvPipelineSummary } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	const [movies, television] = await Promise.all([
		getPipelineSummary(fetch).catch(() => null as PipelineSummary | null),
		getTvSummary(fetch).catch(() => null as TvPipelineSummary | null)
	]);
	return { movies, television };
};
