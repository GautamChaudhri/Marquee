import type { PageLoad } from './$types';
import { getPipelineConfig } from '$lib/api/config';
import { getSettings } from '$lib/api/system';
import type { PipelineConfig } from '$lib/api/config';

export const load: PageLoad = async ({ fetch }) => {
	try {
		const [config, settings] = await Promise.all([
			getPipelineConfig(fetch),
			getSettings(fetch)
		]);
		return { config, settings, error: null as string | null };
	} catch (e) {
		return {
			config: null as PipelineConfig | null,
			settings: null as any,
			error: e instanceof Error ? e.message : 'Failed to load settings'
		};
	}
};
