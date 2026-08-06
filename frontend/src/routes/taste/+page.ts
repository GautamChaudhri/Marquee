import type { PageLoad } from './$types';
import { getTasteStatus } from '$lib/api/taste';
import type { TasteStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch, url }) => {
	const library = (url.searchParams.get('library') as 'movies' | 'tv') || 'movies';
	try {
		return { status: await getTasteStatus(fetch, library), library, error: null as string | null };
	} catch (e) {
		return {
			status: null as TasteStatus | null,
			library,
			error: e instanceof Error ? e.message : 'Failed to load taste status'
		};
	}
};
