/** What a candidate's score is made of, for the score bar and the breakdown beside a poster.
 *
 *  The API gives each ranked candidate its score's parts under the engine's own names. Here
 *  they get plain labels and one of four families, so a card can show a score as a short
 *  stacked bar and the popup can list every part. */
import type { Candidate } from '#lib/api/types.ts';

export type ScorePart = NonNullable<Candidate['score_parts']>[number];

export type Family = 'taste' | 'look' | 'content' | 'source' | 'residual' | 'other';

/** The families in the order a bar stacks them, with what each one means. */
export const FAMILIES: readonly { key: Family; label: string; about: string }[] = [
	{ key: 'taste', label: 'Taste', about: 'How close it is to the posters you chose' },
	{ key: 'look', label: 'Look', about: 'How the image itself looks' },
	{ key: 'content', label: 'Content', about: 'What is on it: faces, and little extra text' },
	{ key: 'source', label: 'Source', about: 'Where it comes from' },
	{ key: 'residual', label: 'Learned', about: 'What your rankings taught on top of the rest' },
	{ key: 'other', label: 'Other', about: 'Parts this page has no name for' }
];

const FEATURES: Record<string, { label: string; family: Family }> = {
	knn_sim: { label: 'Taste match', family: 'taste' },
	dino_knn: { label: 'Style match', family: 'taste' },
	taste_typicality: { label: 'Typical of your picks', family: 'taste' },
	aesthetic: { label: 'Aesthetic', family: 'look' },
	title_colorfulness: { label: 'Title colour', family: 'look' },
	sharpness: { label: 'Sharpness', family: 'look' },
	quality_artifacts: { label: 'Clean file', family: 'look' },
	resolution: { label: 'Resolution', family: 'look' },
	face_area: { label: 'Faces', family: 'content' },
	text_residual: { label: 'Little extra text', family: 'content' },
	provenance: { label: 'Source', family: 'source' },
	official_family: { label: 'Official artwork', family: 'source' },
	lang_match: { label: 'Language', family: 'source' },
	residual: { label: 'Learned from your rankings', family: 'residual' }
};

export function familyOf(feature: string): Family {
	return FEATURES[feature]?.family ?? 'other';
}

/** A part's plain name; a feature this page does not know keeps the engine's name. */
export function featureLabel(feature: string): string {
	return FEATURES[feature]?.label ?? feature;
}

function familyLabel(family: Family): string {
	return FAMILIES.find((entry) => entry.key === family)?.label ?? family;
}

export interface Segment {
	family: Family;
	label: string;
	/** What the family added to the score. */
	amount: number;
	/** Its width as a share of the whole track, from 0 to 1. */
	share: number;
}

/** A score as a stacked bar: one segment per family that added something, in family order.
 *
 *  The segments together are as wide as the score (a score is between 0 and 1), so a longer
 *  bar is a better score. A part that took something away has no segment of its own; the
 *  others shrink together, so the bar still ends at the score. */
export function segments(parts: readonly ScorePart[] | null | undefined): Segment[] {
	const added = new Map<Family, number>();
	let total = 0;
	for (const part of parts ?? []) {
		if (!Number.isFinite(part.contribution)) continue;
		total += part.contribution;
		if (part.contribution > 0) {
			const family = familyOf(part.feature);
			added.set(family, (added.get(family) ?? 0) + part.contribution);
		}
	}
	const positive = [...added.values()].reduce((sum, value) => sum + value, 0);
	const score = Math.min(1, Math.max(0, total));
	if (positive <= 0 || score <= 0) return [];
	return FAMILIES.filter((entry) => added.has(entry.key)).map((entry) => {
		const amount = added.get(entry.key)!;
		return {
			family: entry.key,
			label: entry.label,
			amount,
			share: (amount / positive) * score
		};
	});
}

/** A stacked bar in words, for a reader who cannot see it. */
export function barLabel(bar: readonly Segment[]): string {
	return bar.map((segment) => `${segment.label} ${segment.amount.toFixed(2)}`).join(', ');
}

export interface Row {
	feature: string;
	label: string;
	family: Family;
	familyLabel: string;
	/** How the candidate did on the feature, from 0 to 1; null when the part has no such value. */
	value: number | null;
	contribution: number;
}

/** Every part that moved the score, the largest first. */
export function breakdown(parts: readonly ScorePart[] | null | undefined): Row[] {
	return (parts ?? [])
		.filter((part) => Number.isFinite(part.contribution) && Math.abs(part.contribution) >= 0.0005)
		.map((part) => {
			const family = familyOf(part.feature);
			const value =
				typeof part.value === 'number' && Number.isFinite(part.value)
					? Math.min(1, Math.max(0, part.value))
					: null;
			return {
				feature: part.feature,
				label: featureLabel(part.feature),
				family,
				familyLabel: familyLabel(family),
				value,
				contribution: part.contribution
			};
		})
		.sort((a, b) => b.contribution - a.contribution);
}

/** A contribution with its sign: `+0.23`, `−0.04`. */
export function signed(contribution: number): string {
	const text = Math.abs(contribution).toFixed(2);
	return contribution < 0 ? `−${text}` : `+${text}`;
}
