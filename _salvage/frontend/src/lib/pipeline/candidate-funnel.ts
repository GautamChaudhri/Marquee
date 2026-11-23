/** Candidate funnel derivation for the poster pipeline overview.
 *
 *  The runners record two different kinds of number in `counts` (see
 *  `marquee/pipeline/runner.py`): `*_survivors` keys are how many candidates are
 *  still alive after a stage, while `*_gated` keys are *cumulative rejection
 *  totals*. Only the survivor keys form a monotonic funnel, so those are the only
 *  ones that become bars — mixing the two would draw a funnel that grows.
 */

import type { PipelineMetrics } from '$lib/api/types';

export interface FunnelStage {
	key: string;
	label: string;
	/** What this stage does, for the row tooltip. */
	hint: string;
	count: number;
	/** Share of the funnel head, 0-1. */
	fraction: number;
	/** How many candidates this stage removed relative to the previous one. */
	dropped: number;
	/** The text gate is the stage the poster text profile controls. */
	isTextGate?: boolean;
}

export interface Funnel {
	stages: FunnelStage[];
	head: number;
	ranked: number;
	/** Total candidates rejected across every gate, as recorded by the runner. */
	gated: number;
	windowRuns: number;
}

/** Ordered survivor keys. `total_candidates` only appears on archive-derived runs,
 *  so the head falls back to the post-dedupe count when it is absent. */
const STAGES: { key: string; label: string; hint: string; isTextGate?: boolean }[] = [
	{
		key: 'total_candidates',
		label: 'Fetched',
		hint: 'Candidate artwork downloaded from TMDB.'
	},
	{
		key: 'sha256_survivors',
		label: 'Deduplicated',
		hint: 'Byte-identical duplicates removed.'
	},
	{
		key: 'ocr_survivors',
		label: 'Passed Text Gate',
		hint: 'Survived the OCR text gate set by the active text profile.',
		isTextGate: true
	},
	{
		key: 'phash_survivors',
		label: 'Stacked',
		hint: 'Same-design variants grouped rather than discarded.'
	},
	{
		key: 'feature_survivors',
		label: 'Passed Detail Gates',
		hint: 'Survived face, person, and composition gates.'
	},
	{ key: 'ranked', label: 'Ranked', hint: 'Scored and ordered for selection.' }
];

/** Windows that actually contributed runs — an empty window has no opinion on
 *  whether a stage key exists, so it must not veto that stage. */
function contributing(sources: (PipelineMetrics | null | undefined)[]): PipelineMetrics[] {
	return sources.filter((source): source is PipelineMetrics => (source?.window_runs ?? 0) > 0);
}

function readCount(source: PipelineMetrics, key: string): number | null {
	const value = source.total_counts?.[key];
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Sum a count key across windows, or null when any contributing window omits it.
 *
 * A partial sum is worse than no bar: it silently mixes "films + television" rows
 * with "films only" rows, which draws a funnel that grows at the missing stage.
 */
function sumCounts(sources: PipelineMetrics[], key: string): number | null {
	let total = 0;
	for (const source of sources) {
		const value = readCount(source, key);
		if (value === null) return null;
		total += value;
	}
	return total;
}

/**
 * Build the funnel from one or more metric windows.
 *
 * Returns null when there is nothing to draw — no runs, or every survivor count
 * absent — so callers render an empty state instead of a row of zero-width bars.
 */
export function buildFunnel(sources: (PipelineMetrics | null | undefined)[]): Funnel | null {
	const windows = contributing(sources);
	const windowRuns = windows.reduce((total, source) => total + source.window_runs, 0);
	if (!windowRuns) return null;

	// Only stages every contributing window reports are comparable to each other.
	const comparable = STAGES.map((stage) => ({ ...stage, count: sumCounts(windows, stage.key) }))
		.filter((stage): stage is typeof stage & { count: number } => stage.count !== null)
		.filter((stage) => stage.key !== 'total_candidates' || stage.count > 0);

	const head = comparable[0]?.count ?? 0;
	if (!head) return null;

	let previous = head;
	const stages: FunnelStage[] = [];
	comparable.forEach((stage, index) => {
		const dropped = Math.max(0, previous - stage.count);
		// A stage that removed nothing is a no-op on this host — e.g. `phash_survivors`
		// mirrors `ocr_survivors` whenever STACK_ENABLED groups instead of deleting.
		// Drawing an identical bar twice is noise, so collapse it and keep the ends.
		// The text gate always stays: "rejected nothing" is a real answer about the
		// active text profile, and it is the stage this page exists to explain.
		const keepAlways = index === 0 || index === comparable.length - 1 || stage.isTextGate;
		if (dropped === 0 && !keepAlways) return;
		stages.push({
			key: stage.key,
			label: stage.label,
			hint: stage.hint,
			count: stage.count,
			fraction: head ? stage.count / head : 0,
			dropped,
			isTextGate: stage.isTextGate
		});
		previous = stage.count;
	});

	return {
		stages,
		head,
		ranked: stages[stages.length - 1]?.count ?? 0,
		gated: sumCounts(windows, 'gated') ?? 0,
		windowRuns
	};
}

/** "61%" — share of the funnel head removed by a stage. */
export function dropLabel(stage: FunnelStage, head: number): string {
	if (!head || !stage.dropped) return '';
	return `−${Math.round((stage.dropped / head) * 100)}%`;
}
