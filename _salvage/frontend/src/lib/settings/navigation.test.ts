import { describe, expect, it } from 'vitest';
import { SETTINGS_TABS, readSettingsLocation, settingsQuery } from './navigation';

describe('settings navigation', () => {
	it('keeps the fixed eight-tab information architecture', () => {
		expect(SETTINGS_TABS).toEqual([
			'general',
			'connections',
			'media',
			'posters',
			'pipeline',
			'taste',
			'system',
			'access'
		]);
	});

	it.each(SETTINGS_TABS)('round-trips the %s tab through a shareable URL', (tab) => {
		const query = settingsQuery(tab, tab === 'general' ? 'standard' : 'advanced');
		expect(readSettingsLocation(new URLSearchParams(query))).toEqual({
			tab,
			level: tab === 'general' ? 'standard' : 'advanced'
		});
	});

	it('falls back safely for unknown tab and level values', () => {
		expect(readSettingsLocation(new URLSearchParams('tab=unknown&level=expert'))).toEqual({
			tab: 'general',
			level: 'standard'
		});
	});
});
