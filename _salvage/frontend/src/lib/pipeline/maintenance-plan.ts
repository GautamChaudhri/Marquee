/** The sealed plan a maintenance dry run leaves behind.
 *
 *  Deleting files is two-phase by design: a `dry_run` job walks the scope and
 *  seals it into a checksum, and only a follow-up job quoting that checksum is
 *  allowed to mutate. The checksum lives in the job's `result` document, which
 *  `JobSnapshotResponse` does not carry — so it has to be read with
 *  `getRawDocument(fetch, jobId, 'result')` once the dry run settles.
 */
export type SealedPlan = {
	planChecksum: string;
	plannedCount: number;
	counts: Record<string, number>;
};

/** Category labels as the maintenance handlers name them. */
const CATEGORY_LABELS: Record<string, string> = {
	archives: 'run archives',
	embeddings: 'embedding cache',
	poster_cache: 'poster cache',
	runs_work: 'work files',
	staging: 'staging'
};

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Read a settled dry run's result document, or `null` if it is not one.
 *
 *  A zero-file plan is still a valid plan — an active job makes the handler skip
 *  the work directories — so `plannedCount === 0` must survive parsing rather
 *  than being folded into the failure case.
 */
export function parseSealedPlan(raw: unknown): SealedPlan | null {
	if (!isRecord(raw)) return null;
	const checksum = raw.plan_checksum;
	const planned = raw.planned_count;
	if (typeof checksum !== 'string' || checksum === '') return null;
	if (typeof planned !== 'number' || !Number.isFinite(planned) || planned < 0) return null;
	const counts: Record<string, number> = {};
	if (isRecord(raw.counts)) {
		for (const [category, count] of Object.entries(raw.counts)) {
			if (typeof count === 'number' && Number.isFinite(count)) counts[category] = count;
		}
	}
	return { planChecksum: checksum, plannedCount: planned, counts };
}

/** "20,976 work files · 10,230 embedding cache" — the plan's scope, in prose. */
export function describePlanScope(plan: SealedPlan): string {
	const parts = Object.entries(plan.counts)
		.filter(([, count]) => count > 0)
		.sort(([a], [b]) => a.localeCompare(b))
		.map(([category, count]) => {
			const label = CATEGORY_LABELS[category] ?? category.replace(/_/g, ' ');
			return `${count.toLocaleString()} ${label}`;
		});
	return parts.length > 0 ? parts.join(' · ') : 'Nothing to remove';
}
