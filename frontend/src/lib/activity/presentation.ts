import type { ConnectionState, RecordFreshness } from './store.svelte';
import type {
	CompactProgress,
	JobPresentation,
	MetricCard,
	PresentationAttention,
	PresentationStatus
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
	failed: 'Failed',
	unsafe: 'Unsafe to continue'
};

const CONNECTION_LABELS: Partial<Record<ConnectionState, string>> = {
	initial: 'Connecting',
	loading: 'Loading activity',
	reconnecting: 'Reconnecting',
	stale: 'Progress may be stale',
	incompatible: 'Activity update required',
	stopped: 'Live updates paused'
};

export function activityCallout(
	status: PresentationStatus,
	attention: PresentationAttention,
	progress: CompactProgress | null,
	connection: ConnectionState,
	recordFreshness: RecordFreshness
): ActivityCalloutView | null {
	if (status.phase === 'stopping') {
		return { label: 'Cancelling', message: attention.message ?? null, tone: 'warning' };
	}
	if (attention.reason !== 'none') {
		return {
			label: ATTENTION_LABELS[attention.reason],
			message: attention.message ?? attention.remediation ?? null,
			tone: attention.level === 'error' ? 'negative' : 'warning'
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
