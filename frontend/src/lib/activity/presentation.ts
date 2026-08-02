import type { ConnectionState, RecordFreshness } from './store.svelte';
import type {
	CompactProgress,
	JobPresentation,
	MetricCard,
	PresentationAttention,
	PresentationStatus,
	WorkItemSummary
} from './types';

export type CardTone = 'neutral' | 'active' | 'positive' | 'warning' | 'negative';

export interface ActivityCalloutView {
	label: string;
	message: string | null;
	tone: CardTone;
}

const ATTENTION_LABELS: Record<PresentationAttention['reason'], string> = {
	none: '',
	waiting: 'Waiting',
	held: 'Paused',
	retrying: 'Retrying',
	needs_input: 'Needs attention',
	// The pipeline finished and chose nothing — a decision is owed, which is not the
	// same as something pleasant being ready. "Ready for review" read like the latter.
	review: 'Needs attention',
	failed: 'Failed',
	unsafe: 'Unsafe to continue'
};

// Review is the one state that asks something of the operator, so it gets the same
// amber the status pill uses. It was 'positive' — a tone the callout has no rule for,
// which silently rendered the one actionable line on the card in muted gray.
const ATTENTION_TONES: Partial<Record<PresentationAttention['reason'], CardTone>> = {
	review: 'warning'
};

const CONNECTION_LABELS: Partial<Record<ConnectionState, string>> = {
	initial: 'Connecting',
	loading: 'Loading activity',
	reconnecting: 'Reconnecting',
	stale: 'Progress may be stale',
	incompatible: 'Activity update required',
	stopped: 'Live updates paused'
};

// Attention reasons a finished run's outcome pills state exactly, and better: the pills
// give the whole split ("7 succeeded · 1 needs attention") where the callout could only
// repeat one half of it in prose.
const PILL_COVERED_REASONS = new Set<PresentationAttention['reason']>(['review', 'failed']);

export function activityCallout(
	status: PresentationStatus,
	attention: PresentationAttention,
	progress: CompactProgress | null,
	connection: ConnectionState,
	recordFreshness: RecordFreshness,
	workItems: WorkItemSummary | null = null
): ActivityCalloutView | null {
	if (status.phase === 'stopping') {
		return {
			label: 'Cancelling',
			message: attention.message ?? null,
			tone: 'warning'
		};
	}
	if (attention.reason !== 'none') {
		// A finished run with per-poster outcomes says it all in the pill row above, so
		// the callout stands down. It does not stand down for a whole-group failure —
		// `error` level means every member failed, and that message is a real error
		// summary the counts cannot reproduce.
		if (
			workItems &&
			status.phase === 'terminal' &&
			attention.level !== 'error' &&
			PILL_COVERED_REASONS.has(attention.reason)
		) {
			return null;
		}
		return {
			label: ATTENTION_LABELS[attention.reason],
			message: attention.message ?? attention.remediation ?? null,
			tone:
				ATTENTION_TONES[attention.reason] ?? (attention.level === 'error' ? 'negative' : 'warning')
		};
	}
	if (progress?.wait) {
		return {
			label: 'Waiting',
			message: progress.wait.eligible_at ? `Eligible at ${progress.wait.eligible_at}` : null,
			tone: 'warning'
		};
	}
	if (
		connection === 'reconnecting' ||
		connection === 'stale' ||
		connection === 'incompatible' ||
		connection === 'stopped' ||
		connection === 'initial' ||
		connection === 'loading'
	) {
		return {
			label: CONNECTION_LABELS[connection] ?? 'Activity unavailable',
			message: null,
			tone: connection === 'incompatible' ? 'negative' : 'warning'
		};
	}
	if (recordFreshness === 'stale' || progress?.freshness === 'stale') {
		return { label: 'Progress may be stale', message: null, tone: 'warning' };
	}
	return null;
}

export function metricCards(presentation: JobPresentation | null): MetricCard[] {
	if (!presentation) return [];
	return presentation.sections.flatMap((section) =>
		section.kind === 'metric_cards' ? section.cards : []
	);
}

export function metricText(metric: MetricCard): string {
	const value = metric.value;
	switch (value.type) {
		case 'text':
			return value.text;
		case 'number':
			return `${value.value}${value.unit ? ` ${value.unit}` : ''}`;
		case 'duration':
			return `${value.seconds} s`;
		case 'bytes':
			return `${value.bytes} bytes`;
		case 'timestamp':
			return value.at;
		case 'boolean':
			return value.value ? 'Yes' : 'No';
		case 'badge':
			return value.text;
		case 'subject':
			return value.display_name;
		case 'link':
			return value.label;
	}
	return '';
}
