import { describe, expect, it } from 'vitest';
import type { Candidate } from '#lib/api/types.ts';
import {
	AnswerKeys,
	MAX_SHOWN,
	approvalAnswer,
	bringBack,
	differsFromEngine,
	groupIntoCards,
	hate,
	initialRanking,
	initialShown,
	moveDown,
	moveUp,
	overrideAnswer,
	rankAnswer,
	rankProblem,
	rankingLine,
	rejectAnswer,
	shownIds,
	shownVariant,
	showVariant,
	stackNumber,
	withKey,
	worthRetrying
} from './panel';

function candidate(id: number, rank: number | null, stack: string | null, sha = true): Candidate {
	return {
		id,
		source_file: `c${id}.jpg`,
		sha256: sha ? 'a'.repeat(64) : null,
		stage_reached: 'ranked',
		gate_decision: 'pass',
		rejection_reason: null,
		rank,
		final_score: rank === null ? null : 1 - rank / 100,
		stack,
		is_pick: rank === 1,
		image_url: sha ? `/api/runs/1/candidates/${id}/image` : null
	};
}

// Ranks 1..6: stack 1 has variants ranked 1 and 4, stack 2 ranked 2 and 5, stack 3 ranked 3,
// one ranked 6 has no stack, and one was rejected (no rank).
const RUN = [
	candidate(14, 4, '1B'),
	candidate(11, 1, '1A'),
	candidate(12, 2, '2A'),
	candidate(16, 6, null),
	candidate(13, 3, '3A'),
	candidate(15, 5, '2B'),
	candidate(17, null, null)
];

describe('cards', () => {
	it('reads the stack number from its label', () => {
		expect(stackNumber('3B')).toBe(3);
		expect(stackNumber('12A')).toBe(12);
		expect(stackNumber(null)).toBeNull();
		expect(stackNumber('B')).toBeNull();
	});

	it('groups the ranked candidates into one card per design, in rank order', () => {
		const cards = groupIntoCards(RUN);
		expect(cards.map((c) => c.key)).toEqual(['s1', 's2', 's3', 'c16']);
		expect(cards.map((c) => c.variants.map((v) => v.rank))).toEqual([[1, 4], [2, 5], [3], [6]]);
	});

	it('shows each stack’s best-ranked variant until the owner picks another', () => {
		const cards = groupIntoCards(RUN);
		let shown = initialShown(cards);
		expect(cards.map((c) => shownVariant(c, shown).id)).toEqual([11, 12, 13, 16]);
		shown = showVariant(shown, cards[0]!, 14);
		expect(shownVariant(cards[0]!, shown).id).toBe(14);
		// A candidate of another card is not a variant of this one.
		expect(showVariant(shown, cards[0]!, 12)).toBe(shown);
	});
});

describe('the order and the hate pile', () => {
	const cards = groupIntoCards(RUN);

	it('moves a card up and down, never past either end', () => {
		let ranking = initialRanking(cards);
		ranking = moveUp(ranking, 's3');
		expect(ranking.order).toEqual(['s1', 's3', 's2', 'c16']);
		ranking = moveDown(ranking, 's1');
		expect(ranking.order).toEqual(['s3', 's1', 's2', 'c16']);
		expect(moveUp(ranking, 's3')).toBe(ranking);
		expect(moveDown(ranking, 'c16')).toBe(ranking);
	});

	it('hates a card and brings it back to the end of the order', () => {
		let ranking = hate(initialRanking(cards), 's2');
		expect(ranking).toEqual({ order: ['s1', 's3', 'c16'], hated: ['s2'] });
		ranking = hate(ranking, 's1');
		expect(ranking.hated).toEqual(['s2', 's1']);
		ranking = bringBack(ranking, 's2');
		expect(ranking).toEqual({ order: ['s3', 'c16', 's2'], hated: ['s1'] });
		expect(hate(ranking, 's1')).toBe(ranking);
		expect(bringBack(ranking, 's3')).toBe(ranking);
	});

	it('says whether the ranking differs from the engine’s', () => {
		const start = initialRanking(cards);
		expect(differsFromEngine(start, cards)).toBe(false);
		expect(rankingLine(start, cards)).toBe('Your order is the engine’s order.');
		const hated = hate(start, 's2');
		expect(differsFromEngine(hated, cards)).toBe(true);
		expect(rankingLine(hated, cards)).toBe(
			'Your order keeps the engine’s order. 1 design is in the hate pile.'
		);
		const moved = moveUp(start, 'c16');
		expect(differsFromEngine(moved, cards)).toBe(true);
		expect(rankingLine(moved, cards)).toBe('Your order differs from the engine’s.');
		expect(rankingLine(hate(hate(moved, 's1'), 's2'), cards)).toBe(
			'Your order differs from the engine’s. 2 designs are in the hate pile.'
		);
	});
});

describe('the four answers', () => {
	const cards = groupIntoCards(RUN);
	const shown = showVariant(initialShown(cards), cards[1]!, 15);

	it('Keep the pick sends approval with what was shown', () => {
		expect(withKey(approvalAnswer(cards, shown, true), 'k1')).toEqual({
			action: 'approval',
			shown: [11, 15, 13, 16],
			deploy: true,
			idempotency_key: 'k1'
		});
	});

	it('Use this one sends override with the card’s candidate', () => {
		expect(withKey(overrideAnswer(cards, shown, 15, false), 'k2')).toEqual({
			action: 'override',
			candidate_id: 15,
			shown: [11, 15, 13, 16],
			deploy: false,
			idempotency_key: 'k2'
		});
	});

	it('Save this ranking sends the cards not hated in order, the hate pile, and shown', () => {
		const ranking = hate(moveUp(initialRanking(cards), 's3'), 's1');
		expect(withKey(rankAnswer(cards, shown, ranking, true), 'k3')).toEqual({
			action: 'rank',
			order: [13, 15, 16],
			hated: [11],
			shown: [11, 15, 13, 16],
			deploy: true,
			idempotency_key: 'k3'
		});
	});

	it('None of these sends reject with shown and no deploy', () => {
		const body = withKey(rejectAnswer(cards, shown), 'k4');
		expect(body).toEqual({ action: 'reject', shown: [11, 15, 13, 16], idempotency_key: 'k4' });
		expect('deploy' in body).toBe(false);
	});

	it('shows at most 100 cards’ candidates', () => {
		const many = Array.from({ length: 130 }, (_, i) => candidate(100 + i, i + 1, `${i + 1}A`));
		const big = groupIntoCards(many);
		expect(shownIds(big, initialShown(big))).toHaveLength(MAX_SHOWN);
	});

	it('refuses a ranking whose top design, or a hated one, was never downloaded', () => {
		const odd = groupIntoCards([candidate(1, 1, '1A'), candidate(2, 2, '2A', false)]);
		const oddShown = initialShown(odd);
		const start = initialRanking(odd);
		expect(rankProblem(odd, oddShown, start)).toBeNull();
		expect(rankProblem(odd, oddShown, moveUp(start, 's2'))).toMatch(/never downloaded/);
		expect(rankProblem(odd, oddShown, hate(start, 's2'))).toMatch(/cannot be hated/);
		expect(rankProblem(odd, oddShown, hate(hate(start, 's1'), 's2'))).toMatch(/at least one/);
	});
});

describe('one idempotency key per answer', () => {
	function keys() {
		let n = 0;
		return new AnswerKeys(() => `key-${++n}`);
	}

	it('makes a new key for each answer', () => {
		const k = keys();
		const answer = { action: 'approval', shown: [1] };
		expect(k.keyFor(answer, false)).toBe('key-1');
		k.settled();
		expect(k.keyFor(answer, false)).toBe('key-2');
	});

	it('keeps the key while the same answer is retried after a network failure', () => {
		const k = keys();
		const answer = { action: 'approval', shown: [1] };
		expect(k.keyFor(answer, false)).toBe('key-1');
		expect(k.waiting).toBe(true);
		expect(k.keyFor(answer, true)).toBe('key-1');
		expect(k.keyFor(answer, true)).toBe('key-1');
		k.settled();
		expect(k.waiting).toBe(false);
		expect(k.keyFor(answer, true)).toBe('key-2');
	});

	it('gives a changed answer a new key, even on a retry', () => {
		const k = keys();
		expect(k.keyFor({ action: 'approval' }, false)).toBe('key-1');
		expect(k.keyFor({ action: 'reject' }, true)).toBe('key-2');
		expect(k.keyFor({ action: 'approval' }, false)).toBe('key-3');
	});

	it('retries only what the API may not have seen', () => {
		for (const status of [0, 502, 503, 504]) expect(worthRetrying(status)).toBe(true);
		for (const status of [200, 201, 400, 404, 409, 422]) expect(worthRetrying(status)).toBe(false);
	});
});
