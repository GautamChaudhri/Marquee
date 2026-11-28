/** What the Taste page says: the profile, agreement, the residual, and exemplars. */
import type { Exemplar, TasteStatus, Tally } from '#lib/api/types.ts';

/** The number of blind reviews from which the API gives a percentage. */
export const REVIEWS_FOR_PERCENT = 10;

function plural(n: number, one: string, many = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}

/** The agreement lines, from blind reviews only; reviews that were not blind are apart. */
export function agreementLines(agreement: TasteStatus['agreement']): {
	blind: string[];
	apart: string[];
} {
	const t = agreement.blind;
	const blind: string[] = [];
	if (t.reviewed === 0) {
		blind.push('No blind review yet.');
	} else {
		const share =
			t.percent !== null && t.percent !== undefined ? ` (${Math.round(t.percent)}%)` : '';
		blind.push(
			`You kept the top pick in ${t.agreed} of ${plural(t.reviewed, 'blind review')}${share}.`
		);
		blind.push(`Your choice was in the top 3 in ${t.top3} of ${t.reviewed}.`);
	}
	if (t.percent === null || t.percent === undefined) {
		blind.push(
			`Not enough reviews yet for a percentage (${t.reviewed} of ${REVIEWS_FOR_PERCENT}).`
		);
	}
	const apart: string[] = [];
	if (agreement.not_blind.reviewed > 0) {
		apart.push(
			`${tallyLine(agreement.not_blind, 'review')} of runs that were not blind, counted apart and never in the percentage.`
		);
	}
	if (agreement.unknown.reviewed > 0) {
		apart.push(
			`${tallyLine(agreement.unknown, 'review')} of runs whose profile is not registered, counted apart.`
		);
	}
	return { blind, apart };
}

function tallyLine(t: Tally, what: string): string {
	return `${plural(t.reviewed, what)} (pick kept in ${t.agreed})`;
}

/** Exemplar counts by state, as one line: "Active: 148 liked, 2 disliked · Superseded: 1". */
export function exemplarCounts(counts: TasteStatus['exemplars']): string {
	const order = ['active', 'superseded', 'revoked'];
	const states = Object.keys(counts).sort((a, b) => order.indexOf(a) - order.indexOf(b));
	if (states.length === 0) return 'No exemplars yet.';
	return states
		.map((state) => {
			const byPolarity = counts[state] ?? {};
			const parts = [`${byPolarity.positive ?? 0} liked`, `${byPolarity.negative ?? 0} disliked`];
			return `${state[0]!.toUpperCase()}${state.slice(1)}: ${parts.join(', ')}`;
		})
		.join(' · ');
}

/** How an exemplar came to be, and what ended it if it did. */
export function exemplarSource(exemplar: Pick<Exemplar, 'source' | 'event_id'>): string {
	return exemplar.source === 'seed' ? 'From your library' : `From answer ${exemplar.event_id}`;
}

export function exemplarEnding(
	exemplar: Pick<Exemplar, 'state' | 'ended_by_event_id'>
): string | null {
	if (exemplar.state === 'active') return null;
	const how = exemplar.state === 'revoked' ? 'Revoked' : 'Replaced';
	return exemplar.ended_by_event_id
		? `${how} by answer ${exemplar.ended_by_event_id}`
		: `${how} (before that was recorded)`;
}
