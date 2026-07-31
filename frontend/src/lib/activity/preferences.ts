export type ActivityDensity = 'comfortable' | 'compact';
export type ActivityColumn =
	| 'subject'
	| 'action'
	| 'status'
	| 'progress'
	| 'feature'
	| 'trigger'
	| 'impact'
	| 'time';

export interface ActivityPreferences {
	version: 1;
	density: ActivityDensity;
	columns: ActivityColumn[];
}

export const ACTIVITY_PREFERENCES_KEY = 'marquee:activity:display:v1';
export const MANDATORY_COLUMNS: ActivityColumn[] = ['subject', 'action', 'status'];
// 'attention' used to live here. The card's callout is now the single place a job's
// attention state is stated, so a chip repeating it had nothing left to add.
export const OPTIONAL_COLUMNS: ActivityColumn[] = [
	'progress',
	'feature',
	'trigger',
	'impact',
	'time'
];
const ALL_COLUMNS = new Set<ActivityColumn>([...MANDATORY_COLUMNS, ...OPTIONAL_COLUMNS]);

export const DEFAULT_ACTIVITY_PREFERENCES: ActivityPreferences = {
	version: 1,
	density: 'comfortable',
	columns: [...MANDATORY_COLUMNS, 'progress', 'time']
};

export function normalizeActivityPreferences(value: unknown): ActivityPreferences {
	if (typeof value !== 'object' || value === null) return { ...DEFAULT_ACTIVITY_PREFERENCES };
	const candidate = value as { version?: unknown; density?: unknown; columns?: unknown };
	if (candidate.version !== 1) return { ...DEFAULT_ACTIVITY_PREFERENCES };
	const density: ActivityDensity = candidate.density === 'compact' ? 'compact' : 'comfortable';
	const requested = Array.isArray(candidate.columns)
		? candidate.columns.filter((column): column is ActivityColumn => ALL_COLUMNS.has(column))
		: [];
	return {
		version: 1,
		density,
		columns: [...new Set([...MANDATORY_COLUMNS, ...requested])]
	};
}

export function loadActivityPreferences(storage: Pick<Storage, 'getItem'>): ActivityPreferences {
	try {
		return normalizeActivityPreferences(
			JSON.parse(storage.getItem(ACTIVITY_PREFERENCES_KEY) ?? 'null')
		);
	} catch {
		return { ...DEFAULT_ACTIVITY_PREFERENCES };
	}
}

export function saveActivityPreferences(
	storage: Pick<Storage, 'setItem'>,
	preferences: ActivityPreferences
): void {
	storage.setItem(
		ACTIVITY_PREFERENCES_KEY,
		JSON.stringify(normalizeActivityPreferences(preferences))
	);
}
