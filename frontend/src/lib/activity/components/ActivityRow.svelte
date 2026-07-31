<script lang="ts">
	import type { ActivityColumn, ActivityDensity } from '../preferences';
	import type { ConnectionState, JobRecord } from '../store.svelte';
	import type { CommandResponse, JobRow } from '../types';
	import ActivityActions from './ActivityActions.svelte';
	import BatchExpansion from './BatchExpansion.svelte';
	import JobProgressCard from './JobProgressCard.svelte';
	import PosterProgress from './PosterProgress.svelte';

	type LifecycleAction = 'cancel' | 'pause' | 'resume' | 'change_priority' | 'retry';

	let {
		record,
		columns,
		density,
		connection,
		selectable = false,
		selected,
		onSelected,
		onCommand
	}: {
		record: JobRecord;
		columns: ActivityColumn[];
		density: ActivityDensity;
		connection: ConnectionState;
		/** Checkboxes stay out of the way until the page enters selection mode. */
		selectable?: boolean;
		selected: boolean;
		onSelected: (selected: boolean) => void;
		onCommand: (
			row: JobRow,
			action: LifecycleAction,
			priority?: number
		) => Promise<CommandResponse>;
	} = $props();

	const row = $derived(record.row);
	const workItems = $derived(record.snapshot?.work_items ?? row?.work_items ?? null);
	// The disclosure state lives here, not in PosterProgress, so the button that owns it
	// can sit in the control bar while the panel it reveals renders below.
	let rosterOpen = $state(false);
	const rosterId = $derived(`roster-${record.jobId}`);
	const visible = (column: ActivityColumn) => columns.includes(column);
	const FEATURE_LABELS: Record<string, string> = {
		ai_posters: 'AI posters',
		library_integrations: 'Library',
		ml_taste: 'Taste & ML',
		maintenance: 'Maintenance',
		system: 'System'
	};
	const time = $derived(row?.terminal_at ?? row?.started_at ?? row?.created_at ?? null);
	// The chip row is optional to the point of being empty — every chip in it can be
	// switched off, and time now lives inside the card. An empty bordered strip is worse
	// than no strip, so the row only renders when something is left to put in it.
	const hasSecondary = $derived(
		Boolean(
			row &&
			(visible('feature') ||
				visible('trigger') ||
				(visible('impact') && row.impact) ||
				row.queue_rank != null ||
				row.retry_of_job_id)
		)
	);
</script>

{#if row}
	<article class="activity-row" class:compact={density === 'compact'}>
		{#if selectable}
			<!-- The card heading right below already names the subject; repeating it as
			     visible label text said the same thing twice. -->
			<label class="selection">
				<input
					type="checkbox"
					aria-label={`Select ${row.subject.display_name}`}
					checked={selected}
					onchange={(event) => onSelected(event.currentTarget.checked)}
				/>
			</label>
		{/if}
		<JobProgressCard
			{row}
			snapshot={record.snapshot}
			{connection}
			recordFreshness={record.freshness}
			{workItems}
			showActions={false}
		/>
		<!-- One bar for everything you can do with the row and when it happened. Each of
		     these used to occupy a line of its own, three deep. -->
		<div class="rowbar">
			{#if workItems}
				<button
					type="button"
					class="roster-toggle"
					class:open={rosterOpen}
					aria-expanded={rosterOpen}
					aria-controls={rosterId}
					onclick={() => (rosterOpen = !rosterOpen)}
				>
					<span class="chevron" aria-hidden="true"></span>
					Posters in This Group
				</button>
			{/if}
			<ActivityActions {row} {onCommand} />
			{#if visible('time') && time}
				<time datetime={time}>{new Date(time).toLocaleString()}</time>
			{/if}
		</div>
		{#if hasSecondary}
			<div class="secondary" aria-label="Activity details">
				{#if visible('feature')}<span
						><b>Feature</b>{FEATURE_LABELS[row.feature_area] ?? 'Other'}</span
					>{/if}
				{#if visible('trigger')}<span><b>Trigger</b>{row.trigger.label}</span>{/if}
				{#if visible('impact') && row.impact}
					<span><b>Impact</b>{row.impact.label ?? `${row.impact.items_processed ?? 0} items`}</span>
				{/if}
				{#if row.queue_rank != null}
					<span title="Approximate position within this execution class">
						<b>Class rank</b>≈ {row.queue_rank}
					</span>
				{/if}
				{#if row.retry_of_job_id}
					<a href={`/projection-room/jobs/${row.retry_of_job_id}`}
						><b>Retry of</b>{row.retry_of_job_id}</a
					>
				{/if}
			</div>
		{/if}
		{#if workItems}
			<PosterProgress id={rosterId} jobId={row.job_id} summary={workItems} bind:open={rosterOpen} />
		{:else if row.is_parent}
			<BatchExpansion jobId={row.job_id} />
		{/if}
	</article>
{/if}

<style>
	.activity-row {
		display: grid;
		gap: 9px;
		min-width: 0;
		padding: 8px;
		border: 1px solid var(--line);
		border-radius: calc(var(--radius) + 2px);
		background: color-mix(in srgb, var(--panel) 78%, transparent);
	}
	.activity-row :global(.card) {
		border: 0;
		box-shadow: none;
	}
	.selection {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		justify-self: start;
		padding: 0 8px;
		color: var(--muted);
		font-size: 11px;
	}
	.activity-row.compact :global(.card) {
		gap: 8px;
		padding: 9px;
	}
	.rowbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		padding: 0 8px;
	}
	/* The actions bar carries its own padding for the contexts where it stands alone. */
	.rowbar :global(.actions) {
		padding: 0;
	}
	/* A command that failed has to say so on its own line, not squeeze between buttons. */
	.rowbar :global(.error) {
		flex-basis: 100%;
	}
	.roster-toggle {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 32px;
		padding: 6px 9px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		font-size: 11px;
	}
	/* An explicit chevron: the default disclosure triangle was easy to miss and gave no
	   hover affordance. It points down once the panel below is showing. */
	.chevron {
		width: 0;
		height: 0;
		flex: none;
		border-top: 4px solid transparent;
		border-bottom: 4px solid transparent;
		border-left: 6px solid var(--muted);
		transition: transform 0.15s ease;
	}
	.roster-toggle.open .chevron {
		transform: rotate(90deg);
	}
	/* Pushed right whatever else shares the bar. */
	time {
		margin-left: auto;
		color: var(--muted);
		font-size: 11px;
		font-variant-numeric: tabular-nums;
	}
	.secondary {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		padding: 0 8px;
	}
	.secondary span,
	.secondary a {
		display: inline-flex;
		gap: 5px;
		max-width: 100%;
		padding: 4px 7px;
		border: 1px solid var(--line);
		border-radius: 6px;
		color: var(--muted);
		font-size: 10.5px;
		overflow-wrap: anywhere;
	}
	.secondary b {
		color: var(--muted);
		font-weight: 700;
		text-transform: uppercase;
	}
</style>
