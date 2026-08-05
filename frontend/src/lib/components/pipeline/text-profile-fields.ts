/** Shared vocabulary for the poster text-profile UI — the allow-flag catalog, the
 *  "start from" presets, and the prose summary. Used by the panel, the detail pane
 *  and the editor dialog so the three never drift apart. */

import {
	DEFAULT_PROFILE_SETTINGS,
	type TextProfile,
	type TextProfileScope,
	type TextProfileSettings
} from '$lib/api/text-profiles';

export type AllowKey = Extract<keyof TextProfileSettings, `allow_${string}`>;

/** Which flavour of the editor dialog is open. */
export type ProfileDialogMode = 'create' | 'edit' | 'duplicate';

/** The in-flight edit buffer the dialog hands to the editor. */
export interface ProfileDraft {
	name: string;
	settings: TextProfileSettings;
}

export interface TextCategory {
	key: AllowKey;
	label: string;
	hint: string;
	/** Only meaningful for the season scope. */
	seasonOnly?: boolean;
}

export const TEXT_CATEGORIES: TextCategory[] = [
	{ key: 'allow_title', label: 'Title', hint: 'The title text itself.' },
	{ key: 'allow_director', label: 'Director', hint: 'Directed-by or film-by credits.' },
	{ key: 'allow_studio', label: 'Studio', hint: 'Studio branding like Syncopy or Marvel Studios.' },
	{ key: 'allow_rating', label: 'Rating', hint: 'MPAA or similar rating marks.' },
	{ key: 'allow_tagline', label: 'Tagline', hint: 'Promotional taglines.' },
	{ key: 'allow_billing', label: 'Billing', hint: 'Actor billing strips and credit bands.' },
	{
		key: 'allow_season',
		label: 'Season text',
		hint: 'Season text such as Season 3 or Part 2.',
		seasonOnly: true
	}
];

export function categoriesFor(scope: TextProfileScope): TextCategory[] {
	return TEXT_CATEGORIES.filter((c) => !c.seasonOnly || scope === 'season');
}

export type PresetId = 'title_only' | 'textless' | 'blank';

export const PRESET_OPTIONS: { id: PresetId; label: string; description: string }[] = [
	{ id: 'title_only', label: 'Title Only', description: 'Title yes, everything else off.' },
	{ id: 'textless', label: 'Textless', description: 'No text at all — clean, iconic key art.' },
	{ id: 'blank', label: 'Blank', description: 'Start empty and tune every toggle yourself.' }
];

export function presetSettings(preset: PresetId, scope: TextProfileScope): TextProfileSettings {
	const base: TextProfileSettings = { ...DEFAULT_PROFILE_SETTINGS, mode: 'custom' };
	if (preset === 'textless') {
		// Textless means literally no text: no categories, and no residual slack either.
		return {
			...base,
			allow_title: false,
			require_title: false,
			max_residual_boxes: 0,
			max_residual_area_fraction: 0
		};
	}
	if (preset === 'blank') {
		return { ...base, allow_title: false, require_title: false };
	}
	return scope === 'season' ? { ...base, allow_season: true } : base;
}

/** One-sentence plain-English description of what a profile accepts. */
export function describeProfile(profile: TextProfile, scope: TextProfileScope): string {
	const s = profile.settings;
	const subject = scope === 'movie' ? 'movie' : scope === 'show' ? 'show' : 'season';
	if (s.mode === 'title_only')
		return `Only the ${subject} title is allowed. All other text (taglines, credits, billing blocks) rejects the poster.`;
	if (s.mode === 'textless')
		return 'No text at all. Posters with any detected text are rejected. Use for clean, iconic key art.';
	const allowed = categoriesFor(scope)
		.filter((c) => s[c.key])
		.map((c) => c.label.toLowerCase());
	return [
		allowed.length ? `Allows: ${allowed.join(', ')}` : 'Allows no text categories',
		`residual ≤ ${s.max_residual_boxes} boxes / ${Math.round(s.max_residual_area_fraction * 100)}% area`,
		s.require_title ? 'title required' : 'title optional'
	].join(' · ');
}

export function profileTone(profile: TextProfile): 'blue' | 'gray' | 'gold' {
	if (profile.settings.mode === 'title_only') return 'blue';
	if (profile.settings.mode === 'textless') return 'gray';
	return 'gold';
}
