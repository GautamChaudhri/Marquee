import type { CandidateView, OcrEvidence } from '$lib/api/types';
import { describe, expect, it } from 'vitest';
import {
	ocrConfidenceLabel,
	ocrInspectorPanel,
	ocrRegionLabel,
	ocrSemanticSourceLabel,
	rejectionTag
} from './ocr-display';

const textEvidence: OcrEvidence = {
	available: true,
	has_text: true,
	detected_text: 'EXAMPLE TITLE ONLY IN THEATERS',
	title_matched: true,
	regions: [
		{
			text: 'EXAMPLE TITLE',
			confidence: 0.987,
			category: 'title',
			semantic_source: null,
			semantic_span_text: null,
			is_title: true,
			is_title_fragment: false,
			is_significant: false,
			counts_toward_rejection: false
		},
		{
			text: 'ONLY IN THEATERS',
			confidence: 0.82,
			category: 'tagline',
			semantic_source: null,
			semantic_span_text: null,
			is_title: false,
			is_title_fragment: false,
			is_significant: true,
			counts_toward_rejection: true
		}
	],
	profile: { id: 'title_season_and_name', name: 'Title, Season and Name' },
	error: null
};

function candidate(overrides: Partial<CandidateView> = {}): CandidateView {
	return {
		orig_filename: 'poster.jpg',
		rank: null,
		final_score: null,
		poster_url: '/poster.jpg',
		contributions: null,
		raw_features: null,
		normalized_features: null,
		gate_decision: null,
		gate_reason: null,
		stage_reached: 'ocr',
		rejection_reason: 'text_heavy',
		rejection_label: 'Text heavy',
		rejection_explanation: 'Too much non-title text was detected.',
		ocr_evidence: textEvidence,
		dedup_kept: null,
		stack_id: null,
		stack_rank: null,
		stack_pos: null,
		stack_label: null,
		stack_size: null,
		stack_score: null,
		...overrides
	};
}

describe('OCR rejection display', () => {
	it('uses the compact shared rejection tag', () => {
		expect(rejectionTag(candidate())).toBe('Text heavy');
		expect(rejectionTag(candidate({ rejection_label: 'Format badge' }))).toBe('Format badge');
	});

	it('labels season categories and explains semantic span evidence', () => {
		const region = {
			...textEvidence.regions[1]!,
			text: 'FIVE',
			category: 'season',
			semantic_source: 'overlap_with_phrase',
			semantic_span_text: 'season five'
		};
		expect(ocrRegionLabel(region)).toBe('Season');
		expect(ocrSemanticSourceLabel(region)).toBe('Overlaps SEASON FIVE');
		expect(ocrRegionLabel({ ...region, category: 'season_title' })).toBe('Season name');
		expect(ocrRegionLabel({ ...region, category: 'season_edition' })).toBe('Season edition');
	});

	it('shows captured OCR text and region details when text was found', () => {
		const panel = ocrInspectorPanel(candidate());
		expect(panel).toMatchObject({ kind: 'text', evidence: textEvidence });
		expect(ocrRegionLabel(textEvidence.regions[0]!)).toBe('Title');
		expect(ocrRegionLabel(textEvidence.regions[1]!)).toBe('Tagline');
		expect(ocrConfidenceLabel(0.987)).toBe('99%');
	});

	it('hides evidence for no-text rejections', () => {
		expect(
			ocrInspectorPanel(
				candidate({
					rejection_reason: 'no_text',
					rejection_label: 'No text found',
					ocr_evidence: {
						available: true,
						has_text: false,
						detected_text: '',
						title_matched: false,
						regions: [],
						profile: null,
						error: null
					}
				})
			)
		).toBeNull();
	});

	it('shows the captured OCR failure instead of treating it as no text', () => {
		const panel = ocrInspectorPanel(
			candidate({
				rejection_reason: 'ocr_error: PaddleOCR could not initialize',
				rejection_label: 'OCR error',
				ocr_evidence: {
					available: true,
					has_text: false,
					detected_text: '',
						title_matched: false,
						regions: [],
						profile: null,
						error: 'PaddleOCR could not initialize'
				}
			})
		);
		expect(panel).toEqual({ kind: 'error', message: 'PaddleOCR could not initialize' });
	});

	it('identifies pre-diagnostics OCR runs without claiming no text was found', () => {
		expect(
			ocrInspectorPanel(
				candidate({
					rejection_reason: 'no_title',
					rejection_label: 'Title not matched',
					ocr_evidence: {
						available: false,
						has_text: false,
						detected_text: null,
						title_matched: false,
						regions: [],
						profile: null,
						error: null
					}
				})
			)
		).toEqual({
			kind: 'unavailable',
			message: 'OCR details were not retained for this older run.'
		});
	});
});
