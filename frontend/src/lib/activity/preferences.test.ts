import { describe, expect, it } from 'vitest';
import {
	ACTIVITY_PREFERENCES_KEY,
	loadActivityPreferences,
	normalizeActivityPreferences,
	saveActivityPreferences
} from './preferences';

describe('Activity display preferences', () => {
	it('never permits subject, action, or status to be hidden', () => {
		expect(normalizeActivityPreferences({ version: 1, density: 'compact', columns: [] })).toEqual({
			version: 1,
			density: 'compact',
			columns: ['subject', 'action', 'status']
		});
	});

	it('rejects unknown versions and columns', () => {
		const preferences = normalizeActivityPreferences({
			version: 2,
			density: 'compact',
			columns: ['secrets']
		});
		expect(preferences.version).toBe(1);
		expect(preferences.columns).toContain('subject');
		expect(preferences.columns).not.toContain('secrets');
	});

	it('persists only versioned local display state', () => {
		let stored = '';
		const storage = {
			getItem: (key: string) => (key === ACTIVITY_PREFERENCES_KEY ? stored : null),
			setItem: (_key: string, value: string) => (stored = value)
		};
		saveActivityPreferences(storage, {
			version: 1,
			density: 'compact',
			columns: ['subject', 'action', 'status', 'trigger']
		});
		expect(loadActivityPreferences(storage)).toMatchObject({ density: 'compact' });
	});
});
