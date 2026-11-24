import { describe, expect, it } from 'vitest';
import type { Evaluation, EvaluationResult, EvaluationTally, Residual } from '$lib/api/types';
import {
	compareLine,
	duration,
	headline,
	ordinal,
	outcomeLine,
	progressLine,
	residualLines,
	stateLine
} from './evaluation';

function tally(values: Partial<EvaluationTally> = {}): EvaluationTally {
	return {
		evaluated: 0,
		ranked: 0,
		gated: 0,
		gated_by: {},
		not_offered: 0,
		failed: 0,
		exact: 0,
		same_design: 0,
		top3: 0,
		top5: 0,
		median_card: null,
		mean_reciprocal_card: null,
		...values
	};
}

function evaluation(values: Partial<Evaluation> = {}): Evaluation {
	const empty = tally();
	return {
		id: 3,
		job_id: 90,
		state: 'succeeded',
		profile_id: 2,
		profile_sha256: 'a'.repeat(64),
		residual_id: null,
		scorer: 'weighted',
		movies: 147,
		queued: 147,
		results: 147,
		not_run: 0,
		progress: {},
		created_at: '2026-11-24T12:00:00Z',
		finished_at: '2026-11-24T12:45:00Z',
		summary: { seed: empty, feedback: empty, all: empty },
		...values
	};
}

function result(values: Partial<EvaluationResult>): EvaluationResult {
	return {
		movie_id: 1,
		title: 'Heat',
		year: 1995,
		exemplar_id: 4,
		exemplar_source: 'seed',
		exemplar_image_url: null,
		run_id: 9,
		outcome: 'ranked',
		matched_by: 'cosine',
		match_file: 'm.jpg',
		match_cosine: 0.98,
		best_cosine: 0.98,
		best_file: 'm.jpg',
		unembedded: 0,
		rank: 4,
		ranked_count: 30,
		card: 4,
		cards: 16,
		pick_file: 'p.jpg',
		pick_is_match: false,
		same_design: false,
		stage: null,
		reason: null,
		match_image_url: null,
		pick_image_url: null,
		...values
	};
}

describe('the evaluation in words', () => {
	it('says the headline as a sentence', () => {
		expect(
			headline(
				tally({ evaluated: 147, same_design: 61, top3: 98, gated: 12, not_offered: 9, ranked: 126 })
			)
		).toBe(
			'On 147 posters you picked by hand, the blind pick was your design 61 times (41%); ' +
				'your design was in the first three 98 times; 12 of your posters were removed by a ' +
				'gate; 9 are not among TMDB’s candidates.'
		);
		expect(headline(tally({ evaluated: 1, same_design: 1, top3: 1, gated: 1, failed: 2 }))).toBe(
			'On 1 poster you picked by hand, the blind pick was your design 1 time (100%); your ' +
				'design was in the first three 1 time; 1 of your posters was removed by a gate; 0 are ' +
				'not among TMDB’s candidates; 2 runs failed.'
		);
		expect(headline(tally())).toBe('No poster you picked by hand was evaluated.');
	});

	it('compares, follows, and explains a stop', () => {
		const earlier = evaluation({
			summary: {
				seed: tally({ evaluated: 140, same_design: 50, top3: 90 }),
				feedback: tally(),
				all: tally()
			}
		});
		expect(compareLine(earlier)).toBe(
			'Evaluation 3: your design was the pick 50 of 140 (36%), in the first three 90.'
		);
		expect(
			progressLine(
				evaluation({ state: 'running', results: 11, progress: { succeeded: 11, running: 1 } })
			)
		).toBe('11 of 147 movies done, 1 running; 11 with a result so far.');
		expect(stateLine(evaluation({ state: 'cancelled', not_run: 40 }))).toBe(
			'Cancelled: 40 movies not evaluated.'
		);
		expect(stateLine(evaluation())).toBeNull();
		expect(duration(147)).toBe('about 49 minutes');
		expect(duration(1)).toBe('about a minute');
	});

	it('says what happened to each poster', () => {
		expect(outcomeLine(result({}))).toBe('Your design was 4th of 16.');
		expect(outcomeLine(result({ card: 2, cards: 3 }))).toBe('Your design was 2nd of 3.');
		expect(outcomeLine(result({ pick_is_match: true, same_design: true }))).toBe(
			'Your poster was the blind pick.'
		);
		expect(outcomeLine(result({ same_design: true, card: 1 }))).toBe(
			'Your design was the blind pick (another variant).'
		);
		expect(outcomeLine(result({ outcome: 'gated', stage: 'ocr', reason: 'text_heavy' }))).toBe(
			'Removed by the text gate: text_heavy.'
		);
		expect(outcomeLine(result({ outcome: 'not_offered', best_cosine: 0.912 }))).toBe(
			'Not among TMDB’s candidates. The closest candidate’s likeness was 0.91.'
		);
		expect(outcomeLine(result({ outcome: 'failed', reason: 'TMDB: timed out' }))).toBe(
			'The run failed: TMDB: timed out.'
		);
		expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 101].map(ordinal)).toEqual([
			'1st',
			'2nd',
			'3rd',
			'4th',
			'11th',
			'12th',
			'13th',
			'21st',
			'22nd',
			'101st'
		]);
	});
});

function residual(values: Partial<Residual> = {}): Residual {
	return {
		subjects: 1,
		pairs: 1,
		min_subjects: 25,
		min_pairs: 200,
		trained: false,
		scorer: 'auto',
		in_use: false,
		why: 'the last training did not activate (insufficient_evidence)',
		active: null,
		last_training: {
			id: 1,
			at: '2026-11-24T12:00:00Z',
			outcome: 'no_change',
			reason: 'insufficient_evidence',
			subjects: 1,
			pairs: 1,
			profile_id: 2
		},
		missing: { subjects: 24, pairs: 199 },
		...values
	};
}

describe('the residual in words', () => {
	it('says what is missing when there is too little feedback', () => {
		const lines = residualLines(residual());
		expect(lines.state).toBe(
			'Runs rank with the weighted scorer: the last training did not activate (insufficient_evidence).'
		);
		expect(lines.details).toEqual([
			'Last training 1: changed nothing (insufficient evidence), from 1 movie and 1 pair.',
			'It needs feedback on 24 more movies and 199 more pairs (1 of 25 movies, 1 of 200 pairs).'
		]);
		expect(lines.features).toEqual([]);
		expect(residualLines(residual({ last_training: null })).details[0]).toBe(
			'Not trained yet; a rebuild trains it.'
		);
	});

	it('says what an active residual leans on and how much better it does', () => {
		const lines = residualLines(
			residual({
				in_use: true,
				why: 'a compatible artifact is active',
				trained: true,
				missing: { subjects: 0, pairs: 0 },
				active: {
					id: 4,
					trained_at: '2026-11-24T12:00:00Z',
					profile_id: 3,
					subjects: 30,
					pairs: 900,
					baseline_accuracy: 0.71,
					candidate_accuracy: 0.84,
					held_out_subjects: 6,
					held_out_pairs: 180,
					weights: { aesthetic: 0.2, title_colorfulness: 2.5, knn_sim: -0.4, lang_match: 0 }
				},
				last_training: {
					id: 4,
					at: '2026-11-24T12:00:00Z',
					outcome: 'activate',
					reason: null,
					subjects: 30,
					pairs: 900,
					profile_id: 3
				}
			})
		);
		expect(lines.state).toBe('Runs rank with the residual.');
		expect(lines.details).toEqual([
			'On 6 held-out movies, it agrees with your order 84% of the time, against 71% without it.',
			'Last training 4: activated, from 30 movies and 900 pairs.'
		]);
		expect(lines.features.map((f) => f.name)).toEqual([
			'title_colorfulness',
			'knn_sim',
			'aesthetic'
		]);
	});
});
