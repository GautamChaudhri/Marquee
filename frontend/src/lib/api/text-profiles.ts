/** Text profile API — named OCR text-gate presets for the poster pipeline.
 *  Profiles are scoped by subject: movie, show, or season. */

import { apiGet, apiSend, type Fetch } from './client';

export type TextProfileScope = 'movie' | 'show' | 'season';

export interface TextProfileSettings {
	mode: 'title_only' | 'textless' | 'custom';
	allow_title: boolean;
	allow_director: boolean;
	allow_studio: boolean;
	allow_rating: boolean;
	allow_tagline: boolean;
	allow_billing: boolean;
	allow_season?: boolean;
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

export interface ScopedTextProfileList {
	scopes: Record<TextProfileScope, TextProfileList>;
}

export interface MovieTextProfileSelection {
	profile_id: string | null;
	effective_id: string;
}

export interface SeriesTextProfileSelection {
	show_profile_id: string | null;
	season_profile_id: string | null;
	effective_show: TextProfile;
	effective_season: TextProfile;
}

export const DEFAULT_PROFILE_SETTINGS: TextProfileSettings = {
	mode: 'custom',
	allow_title: true,
	allow_director: false,
	allow_studio: false,
	allow_rating: false,
	allow_tagline: false,
	allow_billing: false,
	allow_season: false,
	max_residual_boxes: 0,
	max_residual_area_fraction: 0.04,
	require_title: true
};

export function listTextProfiles(fetchFn: Fetch): Promise<ScopedTextProfileList> {
	return apiGet<ScopedTextProfileList>(fetchFn, '/text-profiles');
}

export function createTextProfile(
	fetchFn: Fetch,
	scope: TextProfileScope,
	profile: { name: string; settings: TextProfileSettings }
): Promise<TextProfile> {
	return apiSend<TextProfile>(fetchFn, 'POST', `/text-profiles/${scope}`, profile);
}

export function updateTextProfile(
	fetchFn: Fetch,
	scope: TextProfileScope,
	id: string,
	profile: { name?: string; settings?: TextProfileSettings }
): Promise<TextProfile> {
	return apiSend<TextProfile>(fetchFn, 'PUT', `/text-profiles/${scope}/${id}`, profile);
}

export function deleteTextProfile(
	fetchFn: Fetch,
	scope: TextProfileScope,
	id: string
): Promise<{ deleted: string }> {
	return apiSend<{ deleted: string }>(fetchFn, 'DELETE', `/text-profiles/${scope}/${id}`);
}

export function setDefaultProfile(
	fetchFn: Fetch,
	scope: TextProfileScope,
	id: string
): Promise<{ default_id: string }> {
	return apiSend<{ default_id: string }>(fetchFn, 'PUT', `/text-profiles/${scope}/default/${id}`);
}

export function getMovieTextProfile(
	fetchFn: Fetch,
	movieId: number
): Promise<MovieTextProfileSelection> {
	return apiGet(fetchFn, `/text-profiles/movie/${movieId}`);
}

export function setMovieTextProfile(
	fetchFn: Fetch,
	movieId: number,
	profileId: string | null
): Promise<MovieTextProfileSelection> {
	return apiSend(fetchFn, 'PUT', `/text-profiles/movie/${movieId}`, { profile_id: profileId });
}

export function getSeriesTextProfiles(
	fetchFn: Fetch,
	seriesId: number
): Promise<SeriesTextProfileSelection> {
	return apiGet(fetchFn, `/text-profiles/series/${seriesId}`);
}

export function setSeriesTextProfiles(
	fetchFn: Fetch,
	seriesId: number,
	body: { show_profile_id: string | null; season_profile_id: string | null }
): Promise<SeriesTextProfileSelection> {
	return apiSend(fetchFn, 'PUT', `/text-profiles/series/${seriesId}`, body);
}
