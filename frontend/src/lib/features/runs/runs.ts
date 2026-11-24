/** What the movie page says about a run and a deployment. */
import type { Candidate, Deployment, RunDetail } from '$lib/api/types';

/** The ranked candidates, best first. */
export function rankedCandidates(candidates: Candidate[]): Candidate[] {
	return candidates
		.filter((c): c is Candidate & { rank: number } => c.rank !== null && c.rank !== undefined)
		.sort((a, b) => a.rank - b.rank || a.id - b.id);
}

export function blindLabel(blind: boolean | null | undefined): string {
	if (blind === true) return 'Blind';
	if (blind === false) return 'Not blind';
	return 'Blind: unknown';
}

export function runSummary(
	run: Pick<RunDetail, 'state' | 'candidate_count' | 'ranked_count' | 'error' | 'engine_status'>
): string {
	if (run.state === 'running') return 'Running';
	if (run.state === 'failed') return run.error ? `Failed · ${run.error}` : 'Failed';
	if (run.state === 'cancelled') return 'Cancelled';
	const ranked = run.ranked_count ?? 0;
	const considered = run.candidate_count ?? 0;
	if (run.engine_status === 'flagged_manual') {
		return `No candidate passed every gate (${considered} considered)`;
	}
	return `${ranked} ranked of ${considered} considered`;
}

/** One line for what a deployment did. */
export function deploymentSummary(
	d: Pick<
		Deployment,
		| 'action'
		| 'initiated_by'
		| 'new_sha256'
		| 'previous_sha256'
		| 'previous_origin'
		| 'source_file'
		| 'restores_deployment_id'
	>
): string {
	const wrote =
		d.action === 'restore'
			? d.new_sha256
				? `Put back the poster from before deployment ${d.restores_deployment_id}`
				: `Removed the poster, as before deployment ${d.restores_deployment_id}`
			: `Deployed ${d.source_file ?? 'a candidate'}`;
	const by = d.initiated_by === 'policy' ? ' (by the deploy policy)' : '';
	return `${wrote}${by}`;
}

/** What a restore of this deployment would put back. */
export function restoreTarget(d: Pick<Deployment, 'previous_sha256' | 'previous_origin'>): string {
	if (!d.previous_sha256) return 'no poster (the folder had none)';
	return d.previous_origin === 'marquee' ? 'a poster Marquee wrote earlier' : 'your own poster';
}
