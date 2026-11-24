import { describe, expect, it } from 'vitest';
import type { TasteStatus } from '$lib/api/types';
import {
	agreementLines,
	exemplarCounts,
	exemplarEnding,
	exemplarSource,
	residualLine
} from './taste';

const tally = (reviewed: number, agreed: number, top3: number, percent: number | null = null) => ({
	reviewed,
	agreed,
	top3,
	percent
});

function agreement(blind = tally(0, 0, 0), notBlind = tally(0, 0, 0), unknown = tally(0, 0, 0)) {
	return {
		blind,
		by_profile: {},
		by_month: {},
		not_blind: notBlind,
		unknown
	} as TasteStatus['agreement'];
}

describe('agreement', () => {
	it('says when there is no blind review', () => {
		expect(agreementLines(agreement()).blind).toEqual([
			'No blind review yet.',
			'Not enough reviews yet for a percentage (0 of 10).'
		]);
	});

	it('gives counts, and a percentage only when the API gives one', () => {
		expect(agreementLines(agreement(tally(4, 1, 3))).blind).toEqual([
			'You kept the pick in 1 of 4 blind reviews.',
			'Your choice was in the top 3 in 3 of 4.',
			'Not enough reviews yet for a percentage (4 of 10).'
		]);
		expect(agreementLines(agreement(tally(12, 9, 11, 75))).blind).toEqual([
			'You kept the pick in 9 of 12 blind reviews (75%).',
			'Your choice was in the top 3 in 11 of 12.'
		]);
	});

	it('keeps reviews that were not blind apart, never in the percentage', () => {
		const lines = agreementLines(agreement(tally(1, 1, 1), tally(3, 3, 3), tally(1, 0, 0)));
		expect(lines.blind[0]).toBe('You kept the pick in 1 of 1 blind review.');
		expect(lines.apart).toEqual([
			'3 reviews (pick kept in 3) of runs that were not blind, counted apart and never in the percentage.',
			'1 review (pick kept in 0) of runs whose profile is not registered, counted apart.'
		]);
	});
});

describe('the residual and exemplars', () => {
	it('shows the residual against its thresholds', () => {
		expect(
			residualLine({ subjects: 3, pairs: 40, min_subjects: 25, min_pairs: 200, trained: false })
		).toBe('Not trained yet. 3 of 25 titles and 40 of 200 ranked pairs needed.');
	});

	it('counts exemplars by state and polarity', () => {
		expect(
			exemplarCounts({ superseded: { positive: 1 }, active: { positive: 148, negative: 2 } })
		).toBe('Active: 148 liked, 2 disliked · Superseded: 1 liked, 0 disliked');
		expect(exemplarCounts({})).toBe('No exemplars yet.');
	});

	it('says where an exemplar came from and what ended it', () => {
		expect(exemplarSource({ source: 'seed', event_id: null })).toBe('From your library');
		expect(exemplarSource({ source: 'feedback', event_id: 4 })).toBe('From answer 4');
		expect(exemplarEnding({ state: 'active', ended_by_event_id: null })).toBeNull();
		expect(exemplarEnding({ state: 'superseded', ended_by_event_id: 7 })).toBe(
			'Replaced by answer 7'
		);
		expect(exemplarEnding({ state: 'revoked', ended_by_event_id: null })).toBe(
			'Revoked (before that was recorded)'
		);
	});
});
