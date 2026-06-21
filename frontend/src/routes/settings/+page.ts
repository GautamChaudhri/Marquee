import type { PageLoad } from './$types';
import { getPipelineConfig } from '$lib/api/config';
import type { PipelineConfig } from '$lib/api/config';

export const load: PageLoad = async ({ fetch }) => {
	try {
		return { config: await getPipelineConfig(fetch), error: null as string | null };
	} catch (e) {
		return {
			config: null as PipelineConfig | null,
			error: e instanceof Error ? e.message : 'Failed to load pipeline settings'
		};
	}
};
