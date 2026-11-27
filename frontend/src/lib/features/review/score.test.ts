import { describe, expect, it } from 'vitest';
import {
	barLabel,
	breakdown,
	familyOf,
	featureLabel,
	segments,
	signed,
	type ScorePart
} from './score';

const part = (feature: string, contribution: number, value: number | null = null): ScorePart => ({
	feature,
	contribution,
	value
});

describe('a score part’s name and family', () => {
	it('gives the engine’s features plain names', () => {
		expect(featureLabel('knn_sim')).toBe('Taste match');
		expect(familyOf('knn_sim')).toBe('taste');
		expect(familyOf('aesthetic')).toBe('look');
		expect(familyOf('face_area')).toBe('content');
		expect(familyOf('official_family')).toBe('source');
		expect(familyOf('residual')).toBe('residual');
	});

	it('keeps the engine’s name for a feature it does not know', () => {
		expect(featureLabel('new_feature')).toBe('new_feature');
		expect(familyOf('new_feature')).toBe('other');
	});
});

describe('the stacked score bar', () => {
	it('folds the parts into families, in family order, as wide as the score', () => {
		const bar = segments([
			part('aesthetic', 0.05),
			part('knn_sim', 0.2),
			part('face_area', 0.1),
			part('dino_knn', 0.1),
			part('provenance', 0.05),
			part('lang_match', 0)
		]);
		expect(bar.map((segment) => segment.family)).toEqual(['taste', 'look', 'content', 'source']);
		expect(bar[0]!.amount).toBeCloseTo(0.3);
		expect(bar.reduce((sum, segment) => sum + segment.share, 0)).toBeCloseTo(0.5);
		expect(barLabel(bar)).toBe('Taste 0.30, Look 0.05, Content 0.10, Source 0.05');
	});

	it('still ends at the score when a part took something away', () => {
		const bar = segments([part('knn_sim', 0.4), part('aesthetic', 0.2), part('residual', -0.1)]);
		expect(bar.map((segment) => segment.family)).toEqual(['taste', 'look']);
		expect(bar.reduce((sum, segment) => sum + segment.share, 0)).toBeCloseTo(0.5);
		expect(bar[0]!.share / bar[1]!.share).toBeCloseTo(2);
	});

	it('gives the residual its own segment when it added something', () => {
		const bar = segments([part('knn_sim', 0.4), part('residual', 0.1)]);
		expect(bar.map((segment) => segment.label)).toEqual(['Taste', 'Learned']);
	});

	it('is never wider than the track, and empty without parts', () => {
		const wide = segments([part('knn_sim', 0.9), part('aesthetic', 0.6)]);
		expect(wide.reduce((sum, segment) => sum + segment.share, 0)).toBeCloseTo(1);
		expect(segments(null)).toEqual([]);
		expect(segments([])).toEqual([]);
		expect(segments([part('knn_sim', 0)])).toEqual([]);
		expect(segments([part('knn_sim', Number.NaN)])).toEqual([]);
	});
});

describe('the full breakdown', () => {
	it('lists the parts that moved the score, the largest first', () => {
		const rows = breakdown([
			part('aesthetic', 0.04, 0.41),
			part('lang_match', 0, 1),
			part('knn_sim', 0.23, 0.99),
			part('residual', -0.02),
			part('sharpness', 0.0001, 0.2)
		]);
		expect(rows.map((row) => row.label)).toEqual([
			'Taste match',
			'Aesthetic',
			'Learned from your rankings'
		]);
		expect(rows[0]).toMatchObject({ family: 'taste', familyLabel: 'Taste', value: 0.99 });
		expect(rows[2]!.value).toBeNull();
	});

	it('keeps a value between 0 and 1', () => {
		const [high, low] = breakdown([part('knn_sim', 0.3, 1.4), part('aesthetic', 0.1, -0.2)]);
		expect(high!.value).toBe(1);
		expect(low!.value).toBe(0);
		expect(breakdown(undefined)).toEqual([]);
	});

	it('writes a contribution with its sign', () => {
		expect(signed(0.234)).toBe('+0.23');
		expect(signed(-0.04)).toBe('−0.04');
		expect(signed(0)).toBe('+0.00');
	});
});
