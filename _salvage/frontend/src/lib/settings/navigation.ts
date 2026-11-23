import type { SettingsLevel, SettingsTab } from '$lib/api/types';

export const SETTINGS_TABS = [
	'general',
	'connections',
	'media',
	'posters',
	'pipeline',
	'taste',
	'system',
	'access'
] as const satisfies readonly SettingsTab[];

const TAB_SET = new Set<SettingsTab>(SETTINGS_TABS);

export function readSettingsLocation(searchParams: URLSearchParams): {
	tab: SettingsTab;
	level: SettingsLevel;
} {
	const candidate = searchParams.get('tab') as SettingsTab | null;
	return {
		tab: candidate && TAB_SET.has(candidate) ? candidate : 'general',
		level: searchParams.get('level') === 'advanced' ? 'advanced' : 'standard'
	};
}

export function settingsQuery(tab: SettingsTab, level: SettingsLevel): string {
	const params = new URLSearchParams({ tab });
	if (level === 'advanced') params.set('level', level);
	return `?${params.toString()}`;
}
