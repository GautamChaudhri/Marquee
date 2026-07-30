import { describe, expect, it } from 'vitest';
import { describePlanScope, parseSealedPlan } from './maintenance-plan';

/** The shape `_result()` in `handlers_maintenance.py` writes to the job's result document. */
function dryRunResult(overrides: Record<string, unknown> = {}) {
	return {
		outcome: 'no_change',
		operation: 'pipeline_cache_clear',
		message: 'Dry-run plan sealed without mutation.',
		dry_run: true,
		plan_checksum: 'a'.repeat(64),
		planned_count: 31_206,
		processed_count: 0,
		deleted_count: 0,
		counts: { embeddings: 10_230, runs_work: 20_976 },
		cancelled: false,
		backup: null,
		...overrides
	};
}

describe('parseSealedPlan', () => {
	it('reads the checksum and scope out of a settled dry run', () => {
		expect(parseSealedPlan(dryRunResult())).toEqual({
			planChecksum: 'a'.repeat(64),
			plannedCount: 31_206,
			counts: { embeddings: 10_230, runs_work: 20_976 }
		});
	});

	it('keeps an empty plan, which is what an active job produces', () => {
		const plan = parseSealedPlan(dryRunResult({ planned_count: 0, counts: {} }));
		expect(plan).not.toBeNull();
		expect(plan?.plannedCount).toBe(0);
	});

	it('tolerates a missing or non-numeric counts map', () => {
		expect(parseSealedPlan(dryRunResult({ counts: undefined }))?.counts).toEqual({});
		expect(parseSealedPlan(dryRunResult({ counts: { runs_work: 'lots' } }))?.counts).toEqual({});
	});

	it.each([
		['null', null],
		['a string', 'succeeded'],
		['an array', [dryRunResult()]],
		['a checksum-less document', dryRunResult({ plan_checksum: undefined })],
		['an empty checksum', dryRunResult({ plan_checksum: '' })],
		['a countless document', dryRunResult({ planned_count: undefined })],
		['a non-numeric count', dryRunResult({ planned_count: 'many' })],
		['a negative count', dryRunResult({ planned_count: -1 })]
	])('rejects %s', (_label, raw) => {
		expect(parseSealedPlan(raw)).toBeNull();
	});
});

describe('describePlanScope', () => {
	it('names each category with its file count', () => {
		expect(describePlanScope(parseSealedPlan(dryRunResult())!)).toBe(
			'10,230 embedding cache · 20,976 work files'
		);
	});

	it('omits empty categories and falls back when everything is empty', () => {
		const plan = parseSealedPlan(dryRunResult({ counts: { runs_work: 4, staging: 0 } }))!;
		expect(describePlanScope(plan)).toBe('4 work files');
		expect(describePlanScope({ ...plan, counts: {} })).toBe('Nothing to remove');
	});

	it('humanises a category it does not know', () => {
		const plan = parseSealedPlan(dryRunResult({ counts: { future_thing: 2 } }))!;
		expect(describePlanScope(plan)).toBe('2 future thing');
	});
});
