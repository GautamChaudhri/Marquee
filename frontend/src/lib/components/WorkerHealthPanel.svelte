<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { WorkerStatus } from '$lib/api/jobs';
	import StatusDot from './StatusDot.svelte';
	import { durationH } from '$lib/display';

	let { workers }: { workers: WorkerStatus[] } = $props();

	// Mirrors the backend's JOB_HEARTBEAT_SECONDS default (config.py) — not
	// exposed via API today, so this is a reasonable fixed multiple of it
	// rather than a new endpoint just for a staleness threshold.
	const HEARTBEAT_SECONDS = 10;

	let now = $state(Date.now());
	const timer = setInterval(() => (now = Date.now()), 1000);
	onDestroy(() => clearInterval(timer));

	function ageSeconds(heartbeatAt: string): number {
		return (now - new Date(heartbeatAt).getTime()) / 1000;
	}
</script>

<div class="list">
	{#if workers.length === 0}
		<p class="empty">No workers registered.</p>
	{/if}
	{#each workers as w (w.id)}
		{@const age = ageSeconds(w.heartbeat_at)}
		{@const stale = age > HEARTBEAT_SECONDS * 3}
		<div class="row">
			<StatusDot tone={stale ? 'bad' : w.status === 'running' ? 'good' : 'muted'} />
			<span class="id mono">{w.id}</span>
			<span class="status">{w.status}</span>
			<span class="chip mono" class:stale>
				heartbeat {durationH(age)} ago{stale ? ' — stale' : ''}
			</span>
		</div>
	{/each}
</div>

<style>
	.list {
		display: flex;
		flex-direction: column;
	}
	.empty {
		color: var(--muted);
		font-size: 13px;
		margin: 0;
		padding: 8px 0;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line2);
		flex-wrap: wrap;
	}
	.row:last-child {
		border-bottom: none;
	}
	.id {
		font-size: 12.5px;
		color: var(--text);
		margin-right: auto;
	}
	.status {
		font-size: 12px;
		color: var(--muted);
		text-transform: capitalize;
	}
	.chip {
		padding: 2px 8px;
		border-radius: 99px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 11px;
		color: var(--muted);
	}
	.chip.stale {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 30%, transparent);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
