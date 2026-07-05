/** Display-model helpers: map raw API fields → labels, tones, gradients. */
import type { PosterStatus, PosterSummary } from './api/types';

export type Tone =
	| 'good'
	| 'warn'
	| 'low'
	| 'bad'
	| 'info'
	| 'gold'
	| 'dovi'
	| 'muted'
	| 'cpu'
	| 'gpu'
	| 'sampled_clear';

/** Per-film deterministic gradient (handoff §1). hash(title) → palette index. */
export const GRADS: [string, string, string][] = [
	['#1a2744', '#0a1019', '#ffd166'],
	['#2a1535', '#110a1b', '#f472b6'],
	['#2b1a12', '#130905', '#fb923c'],
	['#0e2a25', '#06130e', '#5eead4'],
	['#2a0f17', '#130509', '#fda4af'],
	['#222428', '#0b0c0e', '#fbbf24'],
	['#0f2a3a', '#05121b', '#67e8f9'],
	['#28220c', '#110e05', '#fde047']
];

export function hashStr(s: string): number {
	let h = 2166136261;
	for (let i = 0; i < s.length; i++) {
		h ^= s.charCodeAt(i);
		h = Math.imul(h, 16777619);
	}
	return h >>> 0;
}

export function gradientFor(title: string): [string, string, string] {
	return GRADS[hashStr(title || '?') % GRADS.length];
}

export const posterStatusMeta: Record<PosterStatus, { label: string; tone: Tone }> = {
	deployed: { label: 'Deployed', tone: 'good' },
	approved: { label: 'Approved', tone: 'good' },
	review: { label: 'Review', tone: 'gold' },
	missing: { label: 'No poster', tone: 'bad' }
};

export function posterStatusFromSummary(summary: PosterSummary): PosterStatus {
	if (!summary.has_poster) return 'missing';
	if (summary.deployed_at) return 'deployed';
	if (summary.user_approved) return 'approved';
	return 'review';
}

export function toneVar(tone: Tone): string {
	if (tone === 'sampled_clear') return 'color-mix(in srgb, var(--good) 40%, var(--ink3))';
	return tone === 'muted' ? 'var(--faint)' : `var(--${tone})`;
}

export function letterboxMeta(status: string): { label: string; tone: Tone } | null {
	const map: Record<string, { label: string; tone: Tone }> = {
		candidate: { label: 'Staging', tone: 'warn' },
		prefilter_candidate: { label: 'Candidate', tone: 'gold' },
		prefilter_unknown: { label: 'Needs probe', tone: 'gold' },
		not_letterboxed: { label: 'Cleared candidate', tone: 'muted' },
		tagged: { label: 'Tagged', tone: 'good' },
		reencoded: { label: 'Re-encoded', tone: 'good' },
		variable_unsafe: { label: 'Not letterboxed (variable)', tone: 'muted' },
		skipped: { label: 'Skipped', tone: 'muted' },
		ineligible: { label: 'Ineligible', tone: 'low' },
		errored: { label: 'Error', tone: 'bad' }
	};
	if (status === 'none') return null;
	return map[status] ?? { label: status, tone: 'info' };
}

/** Aspect ratio from source dims, e.g. 1.85. */
export function aspectRatio(w?: number | null, h?: number | null): string | null {
	if (!w || !h) return null;
	return (w / h).toFixed(2);
}

export function bytesH(n: number | null | undefined): string {
	if (n == null) return '—';
	const u = ['B', 'KB', 'MB', 'GB', 'TB'];
	let i = 0;
	let v = n;
	while (v >= 1024 && i < u.length - 1) {
		v /= 1024;
		i++;
	}
	return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

/** Seconds → "2h 14m", "45s", etc. Used for job elapsed/queued/duration display. */
export function durationH(seconds: number | null | undefined): string {
	if (seconds == null || seconds < 0) return '—';
	const s = Math.floor(seconds);
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const sec = s % 60;
	if (h > 0) return `${h}h ${m}m`;
	if (m > 0) return `${m}m ${sec}s`;
	return `${sec}s`;
}
