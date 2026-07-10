import type { Tone } from '$lib/display';
import type { LetterboxTvBucket, LetterboxTvContentType, LetterboxTvVerdict } from '$lib/api/types';

export interface LetterboxTvBucketMeta {
	label: string;
	tone: Tone;
	fill: string;
	border: string;
	foreground: string;
}

export const LETTERBOX_TV_BUCKET_META: Record<LetterboxTvBucket, LetterboxTvBucketMeta> = {
	widescreen: {
		label: 'Widescreen',
		tone: 'good',
		fill: 'var(--good)',
		border: 'color-mix(in srgb, var(--good) 78%, black)',
		foreground: 'var(--on-good)'
	},
	sampled_widescreen: {
		label: 'Sampled Widescreen',
		tone: 'sampled_widescreen',
		fill: 'color-mix(in srgb, var(--good) 62%, var(--panel))',
		border: 'color-mix(in srgb, var(--good) 48%, black)',
		foreground: 'var(--on-good)'
	},
	candidate: {
		label: 'Letterboxed',
		tone: 'warn',
		fill: 'var(--warn)',
		border: 'color-mix(in srgb, var(--warn) 78%, black)',
		foreground: 'var(--on-warn)'
	},
	tagged: {
		label: 'Tagged',
		tone: 'info',
		fill: 'var(--info)',
		border: 'color-mix(in srgb, var(--info) 78%, black)',
		foreground: 'var(--on-info)'
	},
	reencoded: {
		label: 'Reencoded',
		tone: 'gold',
		fill: 'var(--gold)',
		border: 'color-mix(in srgb, var(--gold) 78%, black)',
		foreground: 'var(--on-gold)'
	},
	variable: {
		label: 'Variable',
		tone: 'dovi',
		fill: 'var(--dovi)',
		border: 'color-mix(in srgb, var(--dovi) 78%, black)',
		foreground: 'var(--on-dovi)'
	},
	open_matte: {
		label: 'Open Matte',
		tone: 'teal',
		fill: 'var(--teal)',
		border: 'color-mix(in srgb, var(--teal) 78%, black)',
		foreground: 'var(--on-teal)'
	},
	pillarbox: {
		label: 'Pillarbox',
		tone: 'magenta',
		fill: 'var(--magenta)',
		border: 'color-mix(in srgb, var(--magenta) 78%, black)',
		foreground: 'var(--on-magenta)'
	},
	error: {
		label: 'Error',
		tone: 'bad',
		fill: 'var(--bad)',
		border: 'color-mix(in srgb, var(--bad) 78%, black)',
		foreground: 'var(--on-bad)'
	},
	ineligible: {
		label: 'Ineligible',
		tone: 'muted',
		fill: 'var(--faint2)',
		border: 'var(--line2)',
		foreground: 'var(--text)'
	},
	unanalyzed: {
		label: 'Unanalyzed',
		tone: 'muted',
		fill: 'var(--ink2)',
		border: 'var(--line)',
		foreground: 'var(--muted)'
	}
};

export const LETTERBOX_TV_CONTENT_META: Record<
	LetterboxTvContentType,
	Pick<LetterboxTvBucketMeta, 'label' | 'tone'>
> = {
	widescreen: LETTERBOX_TV_BUCKET_META.widescreen,
	open_matte: LETTERBOX_TV_BUCKET_META.open_matte,
	pillarbox: LETTERBOX_TV_BUCKET_META.pillarbox
};

export const LETTERBOX_TV_VERDICT_META: Record<
	Exclude<LetterboxTvVerdict, 'ok'>,
	{ label: string; tone: Tone }
> = {
	needs_action: { label: 'Needs Action', tone: 'warn' },
	treated: { label: 'Treated', tone: 'info' },
	unanalyzed: { label: 'Unanalyzed', tone: 'muted' }
};
