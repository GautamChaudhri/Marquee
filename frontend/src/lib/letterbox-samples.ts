/**
 * Shared helpers for letterbox sample bar-pair coloring and agree-count computation.
 * Extracted from LetterboxDetail.svelte for reuse in TV episode gallery.
 */

import type { LetterboxSample } from './api/types';

/** Stable color palette — one color per unique top/bottom pair. */
export const BAR_COLORS = [
	'var(--gold)',
	'var(--info)',
	'var(--good)',
	'var(--warn)',
	'var(--bad)',
	'var(--muted)'
];

/** Derive a stable string key from a sample's top/bottom bar values. */
export function pairKey(s: LetterboxSample): string {
	return `${s.top_bar ?? '?'}:${s.bottom_bar ?? '?'}`;
}

/**
 * Build a Map from pair key → color string for all successful (ok) samples.
 * Colors are assigned in stable BAR_COLORS order on first appearance.
 */
export function pairColorMap(samples: LetterboxSample[] | undefined | null): Map<string, string> {
	const seen = new Map<string, string>();
	for (const s of samples ?? []) {
		if (!s.ok) continue;
		const key = pairKey(s);
		if (!seen.has(key)) seen.set(key, BAR_COLORS[seen.size % BAR_COLORS.length]);
	}
	return seen;
}

/**
 * Compute agree-count: count of ok samples sharing the dominant (most common) pair.
 * Returns { agreeCount, totalOk } for building the "All N agree" / "X/Y agree" header.
 */
export function agreeCount(samples: LetterboxSample[] | undefined | null): {
	agreeCount: number;
	totalOk: number;
} {
	const okSamples = (samples ?? []).filter((s) => s.ok);
	if (okSamples.length === 0) return { agreeCount: 0, totalOk: 0 };
	const pairCounts = okSamples.reduce(
		(map, s) => map.set(pairKey(s), (map.get(pairKey(s)) ?? 0) + 1),
		new Map<string, number>()
	);
	const dominant = [...pairCounts.entries()].sort((a, b) => b[1] - a[1])[0];
	return { agreeCount: dominant?.[1] ?? 0, totalOk: okSamples.length };
}
