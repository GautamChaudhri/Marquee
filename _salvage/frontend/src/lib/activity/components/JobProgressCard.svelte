<script lang="ts">
	import type { ConnectionState, RecordFreshness } from '../store.svelte';
	import { activityCallout, metricCards } from '../presentation';
	import type {
		ContainedWorkSummary,
		JobPresentation,
		JobRow,
		JobSnapshotResponse,
		WorkItemSummary
	} from '../types';
	import ActivityCallout from './ActivityCallout.svelte';
	import ConcurrentSubjects from './ConcurrentSubjects.svelte';
	import EvidenceMetrics from './EvidenceMetrics.svelte';
	import ProgressMeasure from './ProgressMeasure.svelte';
	import SubjectHeader from './SubjectHeader.svelte';
	import WorkItemOutcomes from './WorkItemOutcomes.svelte';

	type Variant = 'compact' | 'expanded';
	type CancelHandler = (jobId: string, expectedFenceToken: number) => void | Promise<void>;

	let {
		row,
		snapshot = null,
		presentation = null,
		children = [],
		childrenHasMore = false,
		variant = 'compact',
		connection = 'live',
		recordFreshness = 'live',
		artworkUrl = null,
		activityHref = '/projection-room',
		workItems = null,
		containedWork = null,
		showActions = true,
		onCancel
	}: {
		row: JobRow;
		snapshot?: JobSnapshotResponse | null;
		presentation?: JobPresentation | null;
		children?: JobRow[];
		childrenHasMore?: boolean;
		variant?: Variant;
		connection?: ConnectionState;
		recordFreshness?: RecordFreshness;
		artworkUrl?: string | null;
		/** null when the card is already rendered on the Activity page. */
		activityHref?: string | null;
		/** Per-subject rollup. Drives the outcome pills once the job settles. */
		workItems?: WorkItemSummary | null;
		/** Source-neutral contained-work summary for collection cards. */
		containedWork?: ContainedWorkSummary | null;
		/** False when an ActivityActions bar beside the card already owns this job's
		 *  commands — otherwise Details, Logs, Artifacts and Cancel appear twice. */
		showActions?: boolean;
		onCancel?: CancelHandler;
	} = $props();

	let sendingCancel = $state(false);
	const subject = $derived(presentation?.subject ?? row.subject);
	const actionHeadline = $derived(presentation?.action.headline ?? row.action_headline);
	const status = $derived(snapshot?.status ?? presentation?.status ?? row.status);
	const attention = $derived(snapshot?.attention ?? presentation?.attention ?? row.attention);
	const progress = $derived(snapshot?.progress ?? presentation?.progress ?? row.progress ?? null);
	// Just the name. "Stage 9 of 9" is what the bar's own "9 / 9 stages" count says,
	// and printing both put the same sentence on the card twice.
	const stageName = $derived(progress?.headline ?? progress?.stage_label ?? null);
	// Terminal outcomes that did not run to the end. Everything else reached the last
	// stage, whether or not every subject inside it produced a result.
	const HALTED_OUTCOMES = new Set(['failed', 'dead_letter', 'unsafe', 'cancelled']);
	const actions = $derived(
		snapshot?.allowed_actions ?? presentation?.allowed_actions ?? row.allowed_actions
	);
	const fenceToken = $derived(snapshot?.fence_token ?? null);
	const callout = $derived(
		activityCallout(status, attention, progress, connection, recordFreshness, workItems)
	);
	// A finished job keeps its last measurements on purpose (a failure must show
	// where it stopped, not jump to 100%), so the card — not the server — decides
	// how to draw them. Either signal alone settles it: the record can be terminal
	// before a progress write lands, and a stored progress document is marked
	// terminal even when it is read back through a stale row.
	const settled = $derived(
		status.phase === 'terminal' ||
			progress?.freshness === 'terminal' ||
			recordFreshness === 'terminal'
	);
	// A poster group's title already reads "Get Television Posters · 8 Subjects", which is what
	// the headline would say again in different words. Expanded cards keep it — there the
	// explanation is the point and the subject header is not competing for the same line.
	const showHeadline = $derived(variant === 'expanded' || !containedWork);
	// Counts describe a result, so they wait for one. Mid-run they would be a second,
	// slower progress reading beside the bar.
	const outcomes = $derived(
		status.phase === 'terminal' ? (containedWork ?? workItems ?? null) : null
	);
	// "Finalizing" is a stage, and a finished job is not in one. Once the work is over
	// the bar names the ending instead of the last thing that was happening.
	const barLabel = $derived(
		!settled ? stageName : HALTED_OUTCOMES.has(status.outcome ?? '') ? 'Stopped' : 'Complete'
	);
	// The server stamps every observation with the job's own subject, so on a job
	// that works on one thing — a group chunk included — "Now: …" is the heading
	// above it repeated a size smaller. It only earns its line when the work has
	// moved on to something the heading does not already name.
	const currentSubjectName = $derived(
		progress?.current_subject && progress.current_subject.display_name !== subject.display_name
			? progress.current_subject.display_name
			: null
	);
	const metrics = $derived(metricCards(presentation));
	const ioSummary = $derived.by(() => {
		const values = progress?.metrics;
		if (values?.bytes_processed == null) return null;
		const processed = formatBytes(values.bytes_processed);
		const total = values.bytes_total == null ? null : formatBytes(values.bytes_total);
		const throughput = values.throughput == null ? null : `${formatBytes(values.throughput)}/s`;
		return [total == null ? processed : `${processed} of ${total}`, throughput]
			.filter(Boolean)
			.join(' · ');
	});
	const detailHref = $derived(presentation?.links.detail ?? row.links.detail);
	const logsHref = $derived(presentation?.links.attempts ?? null);
	const artifactsHref = $derived(presentation?.links.artifacts ?? null);
	const canCancel = $derived(actions.includes('cancel') && fenceToken != null && onCancel != null);
	const announcement = $derived(
		[subject.display_name, actionHeadline, status.label, progress?.stage_label]
			.filter((part): part is string => Boolean(part))
			.join('. ')
	);

	async function cancel(): Promise<void> {
		if (!canCancel || fenceToken == null || !onCancel || sendingCancel) return;
		sendingCancel = true;
		try {
			await onCancel(row.job_id, fenceToken);
		} finally {
			sendingCancel = false;
		}
	}

	function formatBytes(value: number): string {
		if (value < 1024) return `${Math.round(value)} B`;
		const units = ['KiB', 'MiB', 'GiB', 'TiB'];
		let scaled = value / 1024;
		let index = 0;
		while (scaled >= 1024 && index < units.length - 1) {
			scaled /= 1024;
			index += 1;
		}
		return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[index]}`;
	}
</script>

<article class="card" class:expanded={variant === 'expanded'} data-tone={status.tone}>
	<div class="announcement" aria-live="polite" aria-atomic="true">{announcement}</div>
	<div class="topline">
		<SubjectHeader {subject} {artworkUrl} compact={variant === 'compact'} />
		<span class="status"><i aria-hidden="true"></i>{status.label}</span>
	</div>

	{#if showHeadline || (stageName && !progress?.overall)}
		<div class="action">
			{#if showHeadline}<strong>{actionHeadline}</strong>{/if}
			{#if stageName && !progress?.overall}
				<!-- Normally the progress bar carries the stage. A job with no overall measure
				     has no bar to carry it, so it falls back to a line of its own here. -->
				<span class="stage">{stageName}</span>
			{/if}
		</div>
	{/if}

	{#if progress?.overall}
		<ProgressMeasure
			measurement={progress.overall}
			label={barLabel}
			fallbackLabel={settled ? 'Complete' : 'Overall progress'}
			tone={settled ? status.tone : null}
			prominent
			{settled}
		/>
	{/if}
	{#if progress?.current && !settled}
		<!-- "Now" is present tense; once the job is over there is no current work,
		     and the outcome callout already carries what happened. -->
		<div class="current-work">
			{#if currentSubjectName}
				<span class="current-subject">Now: {currentSubjectName}</span>
			{/if}
			<ProgressMeasure measurement={progress.current} fallbackLabel="Current work" />
		</div>
	{/if}
	{#if ioSummary}<span class="io-summary">{ioSummary}</span>{/if}

	{#if outcomes}<WorkItemOutcomes summary={outcomes} />{/if}
	{#if callout}<ActivityCallout {callout} />{/if}

	{#if variant === 'expanded'}
		<EvidenceMetrics {metrics} />
		<ConcurrentSubjects {children} hasMore={childrenHasMore} />
	{/if}

	{#if showActions}
		<nav class="actions" aria-label={`Actions for ${subject.display_name}`}>
			{#if activityHref}<a href={activityHref}>Activity</a>{/if}
			<a href={detailHref}>Details</a>
			{#if actions.includes('open_logs') && logsHref}<a href={logsHref}>Logs</a>{/if}
			{#if actions.includes('open_artifacts') && artifactsHref}
				<a href={artifactsHref}>Artifacts</a>
			{/if}
			{#if canCancel}
				<button type="button" onclick={cancel} disabled={sendingCancel}>
					{sendingCancel ? 'Sending…' : 'Cancel'}
				</button>
			{/if}
		</nav>
	{/if}
</article>

<style>
	.card {
		display: grid;
		min-width: 0;
		gap: 12px;
		padding: 13px;
		border: 1px solid var(--line);
		border-left: 3px solid var(--tone-color);
		border-radius: var(--radius);
		background: var(--panel);
		box-shadow: 0 8px 28px color-mix(in srgb, var(--shadow) 18%, transparent);
		--tone-color: var(--muted);
	}

	.io-summary {
		color: var(--muted);
		font-size: 12px;
		font-variant-numeric: tabular-nums;
	}
	.card[data-tone='active'] {
		--tone-color: var(--info);
	}
	.card[data-tone='positive'] {
		--tone-color: var(--good);
	}
	.card[data-tone='warning'] {
		--tone-color: var(--warn);
	}
	.card[data-tone='negative'] {
		--tone-color: var(--bad);
	}
	.expanded {
		gap: 16px;
		padding: 17px;
	}
	.announcement {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}
	.topline {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 12px;
	}
	.status {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		padding: 4px 7px;
		flex: none;
		border: 1px solid var(--line2);
		border-radius: 99px;
		color: var(--muted);
		font-size: 11px;
	}
	.status i {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--tone-color);
	}
	.action {
		display: grid;
		gap: 3px;
	}
	.action strong {
		font-size: 13px;
	}
	.stage,
	.current-subject {
		color: var(--muted);
		font-size: 12px;
	}
	.current-work {
		display: grid;
		gap: 6px;
		padding-top: 10px;
		border-top: 1px solid var(--line);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		padding-top: 2px;
	}
	.actions a,
	.actions button {
		min-height: 32px;
		padding: 6px 10px;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
		font-size: 12px;
	}
	.actions a:hover,
	.actions button:hover:not(:disabled) {
		border-color: var(--gold);
	}
	.actions button {
		margin-left: auto;
		color: var(--bad);
	}
	.actions button:disabled {
		cursor: wait;
		opacity: 0.65;
	}
	@media (max-width: 480px) {
		.card,
		.expanded {
			gap: 11px;
			padding: 11px;
		}
		.topline {
			display: grid;
		}
		.status {
			justify-self: start;
		}
		.actions a,
		.actions button {
			flex: 1 1 auto;
			text-align: center;
		}
		.actions button {
			margin-left: 0;
		}
	}
</style>
