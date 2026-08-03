import type { PageLoad } from './$types';
import { getSettings } from '$lib/api/system';
import type { RuntimeSettings, SettingsLevel, SettingsTab } from '$lib/api/types';
import { readSettingsLocation } from '$lib/settings/navigation';

export const load: PageLoad = async ({ fetch, url }) => {
	const location = readSettingsLocation(url.searchParams);
	const initialTab: SettingsTab = location.tab;
	const initialLevel: SettingsLevel = location.level;
	try {
		return {
			settings: await getSettings(fetch),
			initialTab,
			initialLevel,
			error: null as string | null
		};
	} catch (error) {
		return {
			settings: null as RuntimeSettings | null,
			initialTab,
			initialLevel,
			error: error instanceof Error ? error.message : 'Failed to load settings'
		};
	}
};
