<script lang="ts">
	import type { CommandResponse, JobAction, JobRow } from '../types';

	type LifecycleAction = 'cancel' | 'pause' | 'resume' | 'change_priority' | 'retry';

	let {
		row,
		onCommand
	}: {
		row: JobRow;
		onCommand: (
			row: JobRow,
			action: LifecycleAction,
			priority?: number
		) => Promise<CommandResponse>;
	} = $props();
	let busy = $state<LifecycleAction | null>(null);
	let priority = $derived(row.priority);
	let successorId = $state<string | null>(null);
	let error = $state<string | null>(null);

	const allows = (action: JobAction): boolean => row.allowed_actions.includes(action);

	async function run(action: LifecycleAction): Promise<void> {
		if (!allows(action) || busy) return;
		busy = action;
		error = null;
		try {
			const response = await onCommand(
				row,
				action,
				action === 'change_priority' ? priority : undefined
			);
			successorId = response.replacement_job_id ?? null;
		} catch (reason) {
			error = reason instanceof Error ? reason.message : 'The command could not be accepted.';
		} finally {
			busy = null;
		}
	}
</script>

<div class="actions" role="group" aria-label={`Available commands for ${row.subject.display_name}`}>
	{#if allows('pause')}
		<button type="button" onclick={() => run('pause')} disabled={busy !== null}>Pause</button>
	{/if}
	{#if allows('resume')}
		<button type="button" onclick={() => run('resume')} disabled={busy !== null}>Resume</button>
	{/if}
	{#if allows('cancel')}
		<button class="danger" type="button" onclick={() => run('cancel')} disabled={busy !== null}
			>Cancel</button
		>
	{/if}
	{#if allows('retry')}
		<button type="button" onclick={() => run('retry')} disabled={busy !== null}>Retry</button>
	{/if}
	{#if allows('change_priority')}
		<label>
			<span>Priority · {row.execution_class}</span>
			<input bind:value={priority} type="number" min="0" max="100" step="5" />
		</label>
		<button type="button" onclick={() => run('change_priority')} disabled={busy !== null}
			>Set priority</button
		>
	{/if}
	{#if allows('open_logs')}
		<a href={`${row.links.detail}?tab=logs`}>Logs</a>
	{/if}
	{#if allows('open_artifacts')}
		<a href={`${row.links.detail}?tab=artifacts`}>Artifacts</a>
	{/if}
	{#if successorId}
		<a class="successor" href={`/projection-room/jobs/${successorId}`}>Open retry successor</a>
	{/if}
</div>
{#if error}<p class="error" role="status">{error} Activity has refreshed the current state.</p>{/if}

<style>
	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		padding: 0 8px;
	}
	button,
	a,
	label {
		min-height: 32px;
		padding: 6px 9px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		font-size: 11px;
	}
	label {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		color: var(--muted);
	}
	input {
		width: 58px;
		border: 0;
		background: transparent;
		color: var(--text);
	}
	.danger,
	.error {
		color: var(--bad);
	}
	.successor {
		border-color: var(--good);
		color: var(--good);
	}
	.error {
		margin: 0;
		padding: 0 8px;
		font-size: 11px;
	}
</style>
