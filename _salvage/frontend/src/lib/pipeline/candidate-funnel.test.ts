import { describe, expect, it } from 'vitest';
import { buildFunnel, dropLabel } from './candidate-funnel';
import type { PipelineMetrics } from '$lib/api/types';

function metrics(total: Record<string, number>, windowRuns = 4): PipelineMetrics {
	return {
		window_runs: windowRuns,
		by_status: {},
		by_scorer: {},
		distinct_batches: 1,
		duration_seconds: { avg: null, p50: null, p90: null, max: null },
		avg_counts: {},
		total_counts: total,
		avg_stage_seconds: {},
		total_stage_seconds: {}
	};
}

describe('buildFunnel', () => {
	it('returns null when no runs have happened', () => {
		expect(buildFunnel([metrics({ sha256_survivors: 10 }, 0)])).toBeNull();
		expect(buildFunnel([null, undefined])).toBeNull();
	});

	it('returns null when runs exist but carry no survivor counts', () => {
		expect(buildFunnel([metrics({}, 3)])).toBeNull();
	});

	it('uses the post-dedupe count as the head when total_candidates is absent', () => {
		const funnel = buildFunnel([metrics({ sha256_survivors: 100, ocr_survivors: 40, ranked: 30 })]);
		expect(funnel?.head).toBe(100);
		expect(funnel?.stages[0].label).toBe('Deduplicated');
	});

	it('uses total_candidates as the head when present', () => {
		const funnel = buildFunnel([
			metrics({ total_candidates: 120, sha256_survivors: 100, ocr_survivors: 40, ranked: 30 })
		]);
		expect(funnel?.head).toBe(120);
		expect(funnel?.stages[0].label).toBe('Fetched');
		expect(funnel?.stages[1].dropped).toBe(20);
	});

	it('sums movie and television windows into one funnel', () => {
		const funnel = buildFunnel([
			metrics({ sha256_survivors: 60, ocr_survivors: 20, ranked: 10 }, 3),
			metrics({ sha256_survivors: 40, ocr_survivors: 10, ranked: 5 }, 2)
		]);
		expect(funnel?.windowRuns).toBe(5);
		expect(funnel?.head).toBe(100);
		expect(funnel?.ranked).toBe(15);
	});

	it('collapses a stage that removed nothing', () => {
		// phash mirrors ocr whenever STACK_ENABLED groups variants instead of deleting.
		const funnel = buildFunnel([
			metrics({
				sha256_survivors: 100,
				ocr_survivors: 40,
				phash_survivors: 40,
				feature_survivors: 35,
				ranked: 35
			})
		]);
		expect(funnel?.stages.map((s) => s.key)).toEqual([
			'sha256_survivors',
			'ocr_survivors',
			'feature_survivors',
			'ranked'
		]);
	});

	it('keeps the text gate even when it rejected nothing', () => {
		const funnel = buildFunnel([metrics({ sha256_survivors: 50, ocr_survivors: 50, ranked: 20 })]);
		const textGate = funnel?.stages.find((s) => s.isTextGate);
		expect(textGate).toBeDefined();
		expect(textGate?.dropped).toBe(0);
	});

	it('drops a stage that only some contributing windows report', () => {
		// Regression: summing a key the TV window omits mixed a films-only subtotal in
		// among films+television rows, so the funnel grew at the missing stage.
		const funnel = buildFunnel([
			metrics(
				{ sha256_survivors: 3120, ocr_survivors: 1180, phash_survivors: 1180, ranked: 902 },
				147
			),
			metrics({ sha256_survivors: 1400, ocr_survivors: 520, ranked: 410 }, 62)
		])!;
		expect(funnel.stages.map((s) => s.key)).not.toContain('phash_survivors');
		const counts = funnel.stages.map((s) => s.count);
		expect(counts).toEqual([...counts].sort((a, b) => b - a));
		expect(funnel.ranked).toBe(1312);
	});

	it('ignores an empty window when deciding which stages are comparable', () => {
		// A window with no runs has no opinion, so it must not veto every stage.
		const funnel = buildFunnel([
			metrics({ sha256_survivors: 100, ocr_survivors: 40, ranked: 30 }, 5),
			metrics({}, 0)
		])!;
		expect(funnel.stages.map((s) => s.key)).toEqual([
			'sha256_survivors',
			'ocr_survivors',
			'ranked'
		]);
		expect(funnel.windowRuns).toBe(5);
	});

	it('reports fractions against the head and never a negative drop', () => {
		// A later stage larger than an earlier one would be a backend bug; clamp anyway.
		const funnel = buildFunnel([metrics({ sha256_survivors: 100, ocr_survivors: 25, ranked: 40 })]);
		const ocr = funnel!.stages.find((s) => s.key === 'ocr_survivors')!;
		expect(ocr.fraction).toBeCloseTo(0.25);
		expect(funnel!.stages.find((s) => s.key === 'ranked')!.dropped).toBe(0);
	});

	it('carries the runner gated total through', () => {
		const funnel = buildFunnel([
			metrics({ sha256_survivors: 100, ocr_survivors: 40, ranked: 30, gated: 70 })
		]);
		expect(funnel?.gated).toBe(70);
	});
});

describe('dropLabel', () => {
	it('formats a drop as a share of the head', () => {
		const funnel = buildFunnel([
			metrics({ sha256_survivors: 100, ocr_survivors: 39, ranked: 30 })
		])!;
		const ocr = funnel.stages.find((s) => s.key === 'ocr_survivors')!;
		expect(dropLabel(ocr, funnel.head)).toBe('−61%');
	});

	it('is blank when nothing was dropped', () => {
		const funnel = buildFunnel([metrics({ sha256_survivors: 10, ocr_survivors: 10, ranked: 10 })])!;
		expect(dropLabel(funnel.stages[0], funnel.head)).toBe('');
	});
});
