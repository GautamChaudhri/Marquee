import { describe, expect, it } from 'vitest';
import type { Candidate } from '#lib/api/types.ts';
import { blindLabel, deploymentSummary, rankedCandidates, restoreTarget, runSummary } from './runs';

function candidate(id: number, rank: number | null): Candidate {
	return {
		id,
		source_file: `${id}.jpg`,
		sha256: null,
		stage_reached: null,
		gate_decision: null,
		rejection_reason: rank === null ? 'text_heavy' : null,
		rank,
		final_score: rank === null ? null : 1 - rank / 10,
		stack: null,
		is_pick: rank === 1,
		image_url: null
	};
}

describe('runs', () => {
	it('show the ranked candidates, best first', () => {
		const shown = rankedCandidates([
			candidate(1, 3),
			candidate(2, null),
			candidate(3, 1),
			candidate(4, 2)
		]);
		expect(shown.map((c) => c.id)).toEqual([3, 4, 1]);
	});

	it('summarise a run', () => {
		const done = {
			state: 'completed' as const,
			candidate_count: 40,
			ranked_count: 12,
			error: null,
			engine_status: 'completed'
		};
		expect(runSummary(done)).toBe('12 ranked of 40 considered');
		expect(runSummary({ ...done, engine_status: 'flagged_manual', ranked_count: 0 })).toBe(
			'No candidate passed every gate (40 considered)'
		);
		expect(runSummary({ ...done, state: 'failed', error: 'TMDB answered 500' })).toBe(
			'Failed · TMDB answered 500'
		);
		expect(runSummary({ ...done, state: 'running' })).toBe('Running');
		expect(blindLabel(true)).toBe('Blind');
		expect(blindLabel(false)).toBe('Not blind');
		expect(blindLabel(null)).toBe('Blind: unknown');
	});

	it('say what a deployment did and what restoring it puts back', () => {
		const deploy = {
			action: 'deploy' as const,
			initiated_by: 'owner' as const,
			new_sha256: 'a'.repeat(64),
			previous_sha256: 'b'.repeat(64),
			previous_origin: 'library' as const,
			source_file: 'x.jpg',
			restores_deployment_id: null
		};
		expect(deploymentSummary(deploy)).toBe('Deployed x.jpg');
		expect(deploymentSummary({ ...deploy, initiated_by: 'policy' })).toBe(
			'Deployed x.jpg (by the deploy policy)'
		);
		expect(deploymentSummary({ ...deploy, action: 'restore', restores_deployment_id: 4 })).toBe(
			'Put back the poster from before deployment 4'
		);
		expect(
			deploymentSummary({
				...deploy,
				action: 'restore',
				new_sha256: null,
				restores_deployment_id: 4
			})
		).toBe('Removed the poster, as before deployment 4');
		expect(restoreTarget(deploy)).toBe('your own poster');
		expect(restoreTarget({ ...deploy, previous_origin: 'marquee' })).toBe(
			'a poster Marquee wrote earlier'
		);
		expect(restoreTarget({ previous_sha256: null, previous_origin: null })).toBe(
			'no poster (the folder had none)'
		);
	});
});
