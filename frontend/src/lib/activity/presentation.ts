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
	/** Folded into the label as "Ready for review (3)" when the work items supply it. */
	count: number | null;
}

const ATTENTION_LABELS: Record<PresentationAttention['reason'], string> = {
	none: '',
	waiting: 'Waiting',
	held: 'Paused',
	retrying: 'Retrying',
	needs_input: 'Needs attention',
	review: 'Ready for review',
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

// Attention reasons the work-item summary can count exactly. When it can, the number
// replaces the prose: "Ready for review (3)" says what "3 posters are ready for
// review." said, without restating the label a second time.
const COUNTABLE_REASONS: Partial<Record<PresentationAttention['reason'], 'review_required' | 'failed'>> =
	{
		review: 'review_required',
		failed: 'failed'
	};

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
			tone: 'warning',
			count: null
		};
	}
	if (attention.reason !== 'none') {
		const countKey = COUNTABLE_REASONS[attention.reason];
		const count = countKey && workItems ? (workItems.counts?.[countKey] ?? null) : null;
		return {
			label: ATTENTION_LABELS[attention.reason],
			message: count == null ? (attention.message ?? attention.remediation ?? null) : null,
			tone:
				ATTENTION_TONES[attention.reason] ??
				(attention.level === 'error' ? 'negative' : 'warning'),
			count
		};
	}
	if (progress?.wait) {
		return {
			label: 'Waiting',
			message: progress.wait.eligible_at ? `Eligible at ${progress.wait.eligible_at}` : null,
			tone: 'warning',
			count: null
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
			tone: connection === 'incompatible' ? 'negative' : 'warning',
			count: null
		};
	}
	if (recordFreshness === 'stale' || progress?.freshness === 'stale') {
		return { label: 'Progress may be stale', message: null, tone: 'warning', count: null };
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
