import type { CandidateView, OcrEvidence, OcrEvidenceRegion } from '$lib/api/types';

export type OcrInspectorPanel =
	| { kind: 'text'; evidence: OcrEvidence }
	| { kind: 'error'; message: string }
	| { kind: 'unavailable'; message: string }
	| null;

const NO_TEXT_REASONS = new Set(['no_text', 'ocr_no_text']);

const REGION_LABELS: Record<string, string> = {
	billing: 'Billing',
	director: 'Director',
	other: 'Other text',
	rating: 'Rating',
	season: 'Season',
	season_edition: 'Season edition',
	season_title: 'Season name',
	studio: 'Studio',
	tagline: 'Tagline',
	title: 'Title'
};

function reasonBase(candidate: CandidateView): string {
	return (candidate.rejection_reason ?? '').split(':', 1)[0];
}

/** The concise tag used by every rejected-poster surface. */
export function rejectionTag(candidate: CandidateView): string {
	return (
		candidate.rejection_label ??
		candidate.rejection_explanation ??
		candidate.rejection_reason ??
		candidate.gate_reason ??
		'Rejected'
	);
}

/** Decide which OCR evidence panel, if any, belongs in the shared inspector. */
export function ocrInspectorPanel(candidate: CandidateView): OcrInspectorPanel {
	const evidence = candidate.ocr_evidence;
	if (!evidence) return null;
	if (evidence.error) return { kind: 'error', message: evidence.error };
	if (evidence.has_text) return { kind: 'text', evidence };
	if (NO_TEXT_REASONS.has(reasonBase(candidate))) return null;
	return {
		kind: 'unavailable',
		message: evidence.available
			? 'This run did not retain usable OCR details.'
			: 'OCR details were not retained for this older run.'
	};
}

export function ocrRegionLabel(region: OcrEvidenceRegion): string {
	if (region.is_title) return region.is_title_fragment ? 'Title fragment' : 'Title';
	return region.category ? (REGION_LABELS[region.category] ?? 'Other text') : 'Detected text';
}

export function ocrConfidenceLabel(confidence: number | null): string | null {
	if (confidence == null || !Number.isFinite(confidence)) return null;
	return `${Math.round(Math.max(0, Math.min(1, confidence)) * 100)}%`;
}

export function ocrSemanticSourceLabel(region: OcrEvidenceRegion): string | null {
	if (!region.semantic_source) return null;
	const span = region.semantic_span_text?.toUpperCase();
	switch (region.semantic_source) {
		case 'direct_phrase':
			return span ? `Matched ${span}` : 'Matched season phrase';
		case 'paired_same_line':
			return span ? `Paired as ${span}` : 'Paired on the same line';
		case 'paired_stacked':
			return span ? `Paired as ${span}` : 'Paired vertically';
		case 'overlap_with_phrase':
			return span ? `Overlaps ${span}` : 'Overlaps season phrase';
		case 'same_line_with_phrase':
			return span ? `Paired with ${span}` : 'Paired with season phrase';
		case 'stacked_with_phrase':
			return span ? `Stacked with ${span}` : 'Stacked with season phrase';
		default:
			return 'Matched by season context';
	}
}
