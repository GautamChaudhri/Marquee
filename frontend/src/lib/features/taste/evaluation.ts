/** What the Taste page and the results page say about a blind evaluation and the residual. */
import type { Evaluation, EvaluationResult, EvaluationTally, Residual } from '#lib/api/types.ts';

/** Roughly how long one masked run takes on the GPU, for the confirmation. */
export const SECONDS_PER_MOVIE = 20;

function plural(n: number, one: string, many = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}

function share(count: number, of: number): string {
	return of > 0 ? ` (${Math.round((100 * count) / of)}%)` : '';
}

/** "1st", "2nd", "3rd", "4th", "11th", "22nd". */
export function ordinal(n: number): string {
	const tens = n % 100;
	if (tens >= 11 && tens <= 13) return `${n}th`;
	return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
}

/** The headline, in a sentence a person reads, over the posters picked by hand. */
export function headline(tally: EvaluationTally): string {
	const n = tally.evaluated;
	if (n === 0) return 'No poster you picked by hand was evaluated.';
	const parts = [
		`On ${plural(n, 'poster')} you picked by hand, the blind pick was your design ` +
			`${plural(tally.same_design, 'time')}${share(tally.same_design, n)}`,
		`your design was in the first three ${plural(tally.top3, 'time')}`,
		`${tally.gated} of your posters ${tally.gated === 1 ? 'was' : 'were'} removed by a gate`,
		`${tally.not_offered} ${tally.not_offered === 1 ? 'is' : 'are'} not among TMDB’s candidates`
	];
	if (tally.failed > 0) parts.push(`${plural(tally.failed, 'run')} failed`);
	return `${parts.join('; ')}.`;
}

/** The top line of an earlier evaluation, for comparison. */
export function compareLine(item: Evaluation): string {
	const t = item.summary.seed;
	return (
		`Evaluation ${item.id}: your design was the pick ${t.same_design} of ${t.evaluated}` +
		`${share(t.same_design, t.evaluated)}, in the first three ${t.top3}.`
	);
}

/** How a running evaluation is getting on, from its batch's children. */
export function progressLine(item: Evaluation): string {
	const done = (item.progress.succeeded ?? 0) + (item.progress.failed ?? 0);
	const running = item.progress.running ?? 0;
	return (
		`${done} of ${plural(item.queued, 'movie')} done` +
		`${running ? `, ${running} running` : ''}; ${item.results} with a result so far.`
	);
}

/** Why an evaluation stopped short, when it did. */
export function stateLine(item: Evaluation): string | null {
	if (item.state === 'cancelled') {
		return `Cancelled: ${plural(item.not_run, 'movie')} not evaluated.`;
	}
	if (item.state === 'failed') {
		return `Finished with failures: ${item.summary.all.failed} of ${item.queued} runs failed.`;
	}
	return null;
}

/** About how long an evaluation of `movies` takes, in words. */
export function duration(movies: number): string {
	const minutes = Math.max(1, Math.round((movies * SECONDS_PER_MOVIE) / 60));
	return minutes === 1 ? 'about a minute' : `about ${minutes} minutes`;
}

const STAGES: Record<string, string> = {
	fetch: 'download',
	dedup: 'duplicate',
	resolution: 'resolution',
	style: 'style',
	'style-features': 'style',
	'gate-style': 'style',
	ocr: 'text',
	phash: 'duplicate',
	features: 'final',
	'detail-features': 'final',
	gate: 'final'
};

/** What happened to the owner's poster in one masked run, in words. */
export function outcomeLine(row: EvaluationResult): string {
	switch (row.outcome) {
		case 'ranked': {
			if (row.pick_is_match) return 'Your poster was the blind pick.';
			if (row.same_design) return 'Your design was the blind pick (another variant).';
			return `Your design was ${ordinal(row.card ?? 0)} of ${row.cards}.`;
		}
		case 'gated': {
			const stage = STAGES[row.stage ?? ''] ?? row.stage ?? 'a';
			return `Removed by the ${stage} gate: ${row.reason ?? 'no reason recorded'}.`;
		}
		case 'not_offered': {
			const closest =
				row.best_cosine !== null && row.best_cosine !== undefined
					? ` The closest candidate’s likeness was ${row.best_cosine.toFixed(2)}.`
					: '';
			return `Not among TMDB’s candidates.${closest}`;
		}
		default:
			return `The run failed: ${row.reason ?? 'no reason recorded'}.`;
	}
}

/** The residual, in lines: in use or not and why, held-out accuracy, what it leans on, the
 *  last training, and what is missing. The API's `why` is one plain sentence that already
 *  carries the last training's outcome when that is news, so each fact is said once. */
export function residualLines(residual: Residual): {
	state: string;
	details: string[];
	features: { name: string; weight: number }[];
} {
	const scorer = residual.in_use ? 'the residual' : 'the weighted scorer';
	const state = `Runs rank with ${scorer}: ${residual.why}.`;
	const details: string[] = [];
	const active = residual.active;
	if (active && active.baseline_accuracy != null && active.candidate_accuracy != null) {
		details.push(
			`On ${plural(active.held_out_subjects ?? 0, 'held-out movie')}, it agrees with your ` +
				`order ${Math.round(active.candidate_accuracy * 100)}% of the time, against ` +
				`${Math.round(active.baseline_accuracy * 100)}% without it.`
		);
	}
	const last = residual.last_training;
	if (last) {
		const which = last.id === active?.id ? 'Trained in training' : 'Last training';
		details.push(
			`${which} ${last.id}, from ${plural(last.subjects, 'movie')} and ` +
				`${plural(last.pairs, 'pair')}.`
		);
	}
	const missing = residual.missing;
	if (missing.subjects > 0 || missing.pairs > 0) {
		details.push(
			`It needs feedback on ${plural(missing.subjects, 'more movie')} and ` +
				`${plural(missing.pairs, 'more pair')} (${residual.subjects} of ` +
				`${residual.min_subjects} movies, ${residual.pairs} of ${residual.min_pairs} pairs).`
		);
	}
	const features = Object.entries(active?.weights ?? {})
		.map(([name, weight]) => ({ name, weight }))
		.filter((f) => f.weight !== 0)
		.sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight))
		.slice(0, 6);
	return { state, details, features };
}
