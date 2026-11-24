/** The rank panel's logic: cards from a run's candidates, the owner's order and hate pile,
 *  and the four answers' request bodies. No component state lives here, so every rule can be
 *  tested on its own.
 *
 *  One card is one design: a run's ranked candidates grouped by design stack (`3B` is stack 3,
 *  variant B). A card shows its stack's best-ranked variant until the owner picks another,
 *  and an answer names one candidate per card: the variant the card shows. */
import type { Candidate } from '$lib/api/types';

/** The API keeps at most this many shown candidates per event. */
export const MAX_SHOWN = 100;

export interface Card {
	/** Stable across re-renders: `s<stack>` for a stack, `c<candidate id>` for a lone one. */
	key: string;
	/** The design stack's number, or null for a candidate with none. */
	stack: number | null;
	/** The stack's ranked variants, best first. */
	variants: RankedCandidate[];
}

export type RankedCandidate = Candidate & { rank: number };

/** The design stack's number in a label such as `3B`, or null. */
export function stackNumber(stack: string | null | undefined): number | null {
	const match = /^(\d+)/.exec(stack ?? '');
	return match ? Number(match[1]) : null;
}

/** A run's ranked candidates as cards, in rank order of each card's best variant. */
export function groupIntoCards(candidates: Candidate[]): Card[] {
	const ranked = candidates
		.filter((c): c is RankedCandidate => c.rank !== null && c.rank !== undefined)
		.sort((a, b) => a.rank - b.rank || a.id - b.id);
	const cards: Card[] = [];
	const byStack = new Map<number, Card>();
	for (const candidate of ranked) {
		const stack = stackNumber(candidate.stack);
		if (stack === null) {
			cards.push({ key: `c${candidate.id}`, stack: null, variants: [candidate] });
			continue;
		}
		const card = byStack.get(stack);
		if (card) {
			card.variants.push(candidate);
		} else {
			const fresh: Card = { key: `s${stack}`, stack, variants: [candidate] };
			byStack.set(stack, fresh);
			cards.push(fresh);
		}
	}
	return cards;
}

/** Which variant each card shows, by card key: its best-ranked one unless the owner chose. */
export type Shown = Record<string, number>;

export function initialShown(cards: Card[]): Shown {
	return Object.fromEntries(cards.map((card) => [card.key, card.variants[0]!.id]));
}

/** The variant a card shows. */
export function shownVariant(card: Card, shown: Shown): RankedCandidate {
	return card.variants.find((v) => v.id === shown[card.key]) ?? card.variants[0]!;
}

/** Make another variant of a card the one it shows (and the one an answer names). */
export function showVariant(shown: Shown, card: Card, candidateId: number): Shown {
	if (!card.variants.some((v) => v.id === candidateId)) return shown;
	return { ...shown, [card.key]: candidateId };
}

/** Whether a candidate can be chosen or hated: only one whose file was downloaded. */
export function answerable(candidate: Pick<Candidate, 'sha256'>): boolean {
	return candidate.sha256 !== null && candidate.sha256 !== undefined;
}

// ── The owner's order and the hate pile ───────────────────────────────────

/** Card keys: the owner's order, best first, and the hate pile, in the order hated. */
export interface Ranking {
	order: string[];
	hated: string[];
}

export function initialRanking(cards: Card[]): Ranking {
	return { order: cards.map((card) => card.key), hated: [] };
}

function swap(list: string[], from: number, to: number): string[] {
	if (from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
	const next = [...list];
	[next[from], next[to]] = [next[to]!, next[from]!];
	return next;
}

export function moveUp(ranking: Ranking, key: string): Ranking {
	const at = ranking.order.indexOf(key);
	return at > 0 ? { ...ranking, order: swap(ranking.order, at, at - 1) } : ranking;
}

export function moveDown(ranking: Ranking, key: string): Ranking {
	const at = ranking.order.indexOf(key);
	return at >= 0 && at < ranking.order.length - 1
		? { ...ranking, order: swap(ranking.order, at, at + 1) }
		: ranking;
}

/** Send a card from the order to the hate pile. */
export function hate(ranking: Ranking, key: string): Ranking {
	if (!ranking.order.includes(key)) return ranking;
	return { order: ranking.order.filter((k) => k !== key), hated: [...ranking.hated, key] };
}

/** Bring a card back from the hate pile, to the end of the order. */
export function bringBack(ranking: Ranking, key: string): Ranking {
	if (!ranking.hated.includes(key)) return ranking;
	return { order: [...ranking.order, key], hated: ranking.hated.filter((k) => k !== key) };
}

/** Whether the owner's order or hate pile differs from the engine's ranking. */
export function differsFromEngine(ranking: Ranking, cards: Card[]): boolean {
	if (ranking.hated.length > 0) return true;
	return ranking.order.some((key, i) => key !== cards[i]?.key);
}

/** One line for the panel: how the owner's ranking compares with the engine's. */
export function rankingLine(ranking: Ranking, cards: Card[]): string {
	const kept = cards.map((card) => card.key).filter((key) => ranking.order.includes(key));
	const moved = ranking.order.some((key, i) => key !== kept[i]);
	const hated = ranking.hated.length;
	const pile =
		hated === 1 ? ' 1 design is in the hate pile.' : ` ${hated} designs are in the hate pile.`;
	if (!moved && hated === 0) return 'Your order is the engine’s order.';
	if (!moved) return `Your order keeps the engine’s order.${pile}`;
	return `Your order differs from the engine’s.${hated ? pile : ''}`;
}

// ── The four answers ──────────────────────────────────────────────────────

export type Action = 'approval' | 'override' | 'rank' | 'reject';

/** An answer as the page builds it; `withKey` adds its idempotency key. */
export interface Answer {
	action: Action;
	candidate_id?: number;
	order?: number[];
	hated?: number[];
	shown: number[];
	deploy?: boolean;
}

export type FeedbackBody = Answer & { idempotency_key: string };

export function withKey(answer: Answer, key: string): FeedbackBody {
	return { ...answer, idempotency_key: key };
}

function byKey(cards: Card[]): Map<string, Card> {
	return new Map(cards.map((card) => [card.key, card]));
}

/** Every card's shown candidate, in the order the page first showed the cards (rank order),
 *  at most MAX_SHOWN. */
export function shownIds(cards: Card[], shown: Shown): number[] {
	return cards.slice(0, MAX_SHOWN).map((card) => shownVariant(card, shown).id);
}

function idsOf(keys: string[], cards: Card[], shown: Shown): number[] {
	const lookup = byKey(cards);
	return keys
		.map((key) => lookup.get(key))
		.filter((card): card is Card => card !== undefined)
		.map((card) => shownVariant(card, shown).id);
}

/** "Keep the pick". */
export function approvalAnswer(cards: Card[], shown: Shown, deploy: boolean): Answer {
	return { action: 'approval', shown: shownIds(cards, shown), deploy };
}

/** "Use this one", on any card. */
export function overrideAnswer(
	cards: Card[],
	shown: Shown,
	candidateId: number,
	deploy: boolean
): Answer {
	return { action: 'override', candidate_id: candidateId, shown: shownIds(cards, shown), deploy };
}

/** "Save this ranking": the cards not hated, in the owner's order, and the hate pile. */
export function rankAnswer(cards: Card[], shown: Shown, ranking: Ranking, deploy: boolean): Answer {
	return {
		action: 'rank',
		order: idsOf(ranking.order, cards, shown),
		hated: idsOf(ranking.hated, cards, shown),
		shown: shownIds(cards, shown),
		deploy
	};
}

/** "None of these": the API hates every shown candidate and keeps the best-ranked 8. */
export function rejectAnswer(cards: Card[], shown: Shown): Answer {
	return { action: 'reject', shown: shownIds(cards, shown) };
}

/** Why "Save this ranking" cannot be sent now, or null when it can. */
export function rankProblem(cards: Card[], shown: Shown, ranking: Ranking): string | null {
	const lookup = byKey(cards);
	const first = lookup.get(ranking.order[0] ?? '');
	if (!first) return 'Keep at least one design out of the hate pile.';
	if (!answerable(shownVariant(first, shown))) {
		return 'The design at the top was never downloaded, so it cannot be chosen. Move another one up.';
	}
	for (const key of ranking.hated) {
		const card = lookup.get(key);
		if (card && !answerable(shownVariant(card, shown))) {
			return 'A design in the hate pile was never downloaded, so it cannot be hated.';
		}
	}
	return null;
}

// ── One idempotency key per answer ────────────────────────────────────────

/** The key of the answer being sent. A new answer gets a new key; retrying the same answer
 *  after a network failure keeps it, so the API records it once. */
export class AnswerKeys {
	private pending: { body: string; key: string } | null = null;

	constructor(private readonly make: () => string) {}

	/** The key for this answer: the pending one when this is a retry of it, else a new one. */
	keyFor(answer: unknown, retry: boolean): string {
		const body = JSON.stringify(answer);
		if (retry && this.pending && this.pending.body === body) return this.pending.key;
		this.pending = { body, key: this.make() };
		return this.pending.key;
	}

	/** The answer reached the API (recorded, or refused for a reason a retry cannot change):
	 *  the next answer gets a new key. */
	settled(): void {
		this.pending = null;
	}

	get waiting(): boolean {
		return this.pending !== null;
	}
}

/** Whether a failed request may be sent again with the same key: the API may not have seen
 *  it, or did not answer. A refusal (404, 409, 422) is final for that answer. */
export function worthRetrying(status: number): boolean {
	return status === 0 || status === 502 || status === 503 || status === 504;
}
