<script lang="ts">
	import type { ActivityColumn, ActivityDensity } from '../preferences';
	import type { ConnectionState, JobRecord } from '../store.svelte';
	import type { CommandResponse, JobRow } from '../types';
	import ActivityActions from './ActivityActions.svelte';
	import BatchExpansion from './BatchExpansion.svelte';
	import JobProgressCard from './JobProgressCard.svelte';

	type LifecycleAction = 'cancel' | 'pause' | 'resume' | 'change_priority' | 'retry';

	let {
		record,
		columns,
		density,
		connection,
		selected,
		onSelected,
		onCommand
	}: {
		record: JobRecord;
		columns: ActivityColumn[];
		density: ActivityDensity;
		connection: ConnectionState;
		selected: boolean;
		onSelected: (selected: boolean) => void;
		onCommand: (
			row: JobRow,
			action: LifecycleAction,
			priority?: number
		) => Promise<CommandResponse>;
	} = $props();

	const row = $derived(record.row);
	const visible = (column: ActivityColumn) => columns.includes(column);
	const FEATURE_LABELS: Record<string, string> = {
		ai_posters: 'AI posters',
		hdr: 'HDR / Dolby Vision',
		audio_subtitles: 'Audio & subtitles',
		letterbox: 'Letterbox',
		library_integrations: 'Library',
		ml_taste: 'Taste & ML',
		maintenance: 'Maintenance',
		system: 'System'
	};
	const time = $derived(row?.terminal_at ?? row?.started_at ?? row?.created_at ?? null);
</script>

{#if row}
	<article class="activity-row" class:compact={density === 'compact'}>
		<label class="selection">
			<input
				type="checkbox"
				checked={selected}
				onchange={(event) => onSelected(event.currentTarget.checked)}
			/>
			Select {row.subject.display_name}
		</label>
		<JobProgressCard
			{row}
			snapshot={record.snapshot}
			{connection}
			recordFreshness={record.freshness}
		/>
		<ActivityActions {row} {onCommand} />
		<div class="secondary" aria-label="Activity details">
			{#if visible('feature')}<span
					><b>Feature</b>{FEATURE_LABELS[row.feature_area] ?? 'Other'}</span
				>{/if}
			{#if visible('trigger')}<span><b>Trigger</b>{row.trigger.label}</span>{/if}
			{#if visible('attention') && row.attention.message}
				<span data-tone={row.attention.level}><b>Attention</b>{row.attention.message}</span>
			{/if}
			{#if visible('impact') && row.impact}
				<span><b>Impact</b>{row.impact.label ?? `${row.impact.items_processed ?? 0} items`}</span>
			{/if}
			{#if visible('time') && time}<span><b>Time</b>{new Date(time).toLocaleString()}</span>{/if}
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
		{#if row.is_parent}<BatchExpansion jobId={row.job_id} />{/if}
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
		color: var(--faint2);
		font-weight: 700;
		text-transform: uppercase;
	}
	.secondary [data-tone='warning'] {
		border-color: color-mix(in srgb, var(--warn) 40%, var(--line));
	}
	.secondary [data-tone='error'] {
		border-color: color-mix(in srgb, var(--bad) 40%, var(--line));
	}
</style>
