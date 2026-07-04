/** Shared label/tone maps for the TV HDR surfaces (landing, /hdr/tv, /hdr/tv/[id]). */
import type { Tone } from './display';
import type { RadarrOverlayStatus, ShowStatus, ShowUniformity } from './api/types';

export const HDR_DISTRIBUTION_ORDER = [
	'sdr',
	'hdr',
	'hdr10',
	'hdr10p',
	'dovi',
	'dovi_no_fallback'
] as const;

export const HDR_TAG_LABEL: Record<string, string> = {
	sdr: 'SDR',
	hdr: 'HDR',
	hdr10: 'HDR10',
	hdr10p: 'HDR10+',
	dovi: 'DoVi',
	dovi_no_fallback: 'DoVi w/o fallback'
};

export const HDR_DIST_TONE: Record<string, Tone> = {
	sdr: 'muted',
	hdr: 'good',
	hdr10: 'info',
	hdr10p: 'dovi',
	dovi: 'gold',
	dovi_no_fallback: 'bad'
};

export const MOVIE_STATUS_META: Record<RadarrOverlayStatus, { label: string; tone: Tone }> = {
	below_target: { label: 'Below target', tone: 'bad' },
	meets_target: { label: 'Meets target', tone: 'good' },
	exceeds_target: { label: 'Exceeds target', tone: 'gold' },
	no_hdr_target: { label: 'No HDR target', tone: 'muted' }
};

export const SHOW_STATUS_META: Record<ShowStatus, { label: string; tone: Tone }> = {
	exceeds_target: { label: 'Exceeds target', tone: 'gold' },
	meets_target: { label: 'Meets target', tone: 'good' },
	gaps: { label: 'Gaps', tone: 'warn' },
	below_target: { label: 'Below target', tone: 'bad' },
	no_hdr_target: { label: 'No HDR target', tone: 'muted' },
	unknown: { label: 'Unknown', tone: 'low' }
};

export const UNIFORMITY_META: Record<ShowUniformity, { label: string }> = {
	uniform: { label: 'Uniform' },
	uniform_by_season: { label: 'By season' },
	mixed: { label: 'Mixed' }
};
