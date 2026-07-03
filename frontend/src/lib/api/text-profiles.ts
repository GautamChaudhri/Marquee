/** Text profile API — named OCR text-gate presets for the poster pipeline.
 *  Built-ins (title_only / textless) are immutable; custom profiles are
 *  user-created. One profile is the global default; movies may override it. */

import { apiGet, apiSend, type Fetch } from './client';

export interface TextProfileSettings {
	mode: 'title_only' | 'textless' | 'custom';
	allow_title: boolean;
	allow_director: boolean;
	allow_studio: boolean;
	allow_rating: boolean;
	allow_tagline: boolean;
	allow_billing: boolean;
	max_residual_boxes: number;
	max_residual_area_fraction: number;
	require_title: boolean;
}

export interface TextProfile {
	id: string;
	name: string;
	builtin: boolean;
	is_default: boolean;
	settings: TextProfileSettings;
}

export interface TextProfileList {
	profiles: TextProfile[];
	default_id: string;
}

export const DEFAULT_PROFILE_SETTINGS: TextProfileSettings = {
	mode: 'custom',
	allow_title: true,
	allow_director: false,
	allow_studio: false,
	allow_rating: false,
	allow_tagline: false,
	allow_billing: false,
	max_residual_boxes: 0,
	max_residual_area_fraction: 0.04,
	require_title: true
};

export function listTextProfiles(fetchFn: Fetch): Promise<TextProfileList> {
	return apiGet<TextProfileList>(fetchFn, '/text-profiles');
}

export function createTextProfile(
	fetchFn: Fetch,
	profile: { name: string; settings: TextProfileSettings }
): Promise<TextProfile> {
	return apiSend<TextProfile>(fetchFn, 'POST', '/text-profiles', profile);
}

export function updateTextProfile(
	fetchFn: Fetch,
	id: string,
	profile: { name?: string; settings?: TextProfileSettings }
): Promise<TextProfile> {
	return apiSend<TextProfile>(fetchFn, 'PUT', `/text-profiles/${id}`, profile);
}

export function deleteTextProfile(fetchFn: Fetch, id: string): Promise<{ deleted: string }> {
	return apiSend<{ deleted: string }>(fetchFn, 'DELETE', `/text-profiles/${id}`);
}

export function setDefaultProfile(fetchFn: Fetch, id: string): Promise<{ default_id: string }> {
	return apiSend<{ default_id: string }>(fetchFn, 'PUT', `/text-profiles/default/${id}`);
}

export function getMovieTextProfile(
	fetchFn: Fetch,
	movieId: number
): Promise<{ movie_id: number; profile_id: string | null; effective_id: string }> {
	return apiGet(fetchFn, `/text-profiles/movie/${movieId}`);
}

export function setMovieTextProfile(
	fetchFn: Fetch,
	movieId: number,
	profileId: string | null
): Promise<{ movie_id: number; profile_id: string | null; effective_id: string }> {
	return apiSend(fetchFn, 'PUT', `/text-profiles/movie/${movieId}`, { profile_id: profileId });
}
