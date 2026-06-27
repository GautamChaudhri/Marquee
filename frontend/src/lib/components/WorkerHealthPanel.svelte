<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { WorkerStatus } from '$lib/api/jobs';
	import { durationH } from '$lib/display';
	import StatusDot from './StatusDot.svelte';

	let { workers }: { workers: WorkerStatus[] } = $props();

	let now = $state(Date.now());
	const timer = setInterval(() => (now = Date.now()), 1000);
	onDestroy(() => clearInterval(timer));

	function ageSeconds(heartbeatAt: string): number {
		return (now - new Date(heartbeatAt).getTime()) / 1000;
	}

	function roleFor(worker: WorkerStatus, index: number): string {
		if (worker.id.includes('scheduler')) return 'Scheduler';
		return index === 0 ? 'Primary Worker' : `Worker ${index + 1}`;
	}

	function hostFor(worker: WorkerStatus): string {
		return worker.id.split('-')[0] ?? worker.id;
	}
	const hostCount = $derived(new Set(workers.map((worker) => hostFor(worker))).size);
</script>

<div class="list">
	{#if workers.length === 0}
		<p class="empty">No live workers registered.</p>
	{:else}
		<p class="summary">{workers.length} live worker{workers.length === 1 ? '' : 's'} across {hostCount} host{hostCount === 1 ? '' : 's'}.</p>
	{/if}
	{#each workers as worker, index (worker.id)}
		<div class="row">
			<StatusDot tone={worker.status === 'running' ? 'good' : 'warn'} />
			<div class="meta">
				<span class="role">{roleFor(worker, index)}</span>
				<span class="id mono">{worker.id}</span>
			</div>
			<span class="host mono">{hostFor(worker)}</span>
			<span class="chip">{worker.status}</span>
			<span class="heartbeat mono">heartbeat {durationH(ageSeconds(worker.heartbeat_at))} ago</span>
		</div>
	{/each}
</div>

<style>
	.list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.summary,
	.empty {
		color: var(--muted);
		font-size: 12px;
		margin: 0;
	}
	.row {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto auto auto;
		align-items: center;
		gap: 10px;
		padding-bottom: 10px;
		border-bottom: 1px solid var(--line2);
	}
	.row:last-child {
		border-bottom: none;
		padding-bottom: 0;
	}
	.meta {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.role {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--text);
	}
	.id {
		font-size: 11px;
		color: var(--faint);
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.host,
	.heartbeat {
		font-size: 11px;
		color: var(--muted);
	}
	.chip {
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 11px;
		color: var(--muted);
		text-transform: capitalize;
	}
	.mono {
		font-family: var(--font-mono);
	}
	@media (max-width: 900px) {
		.row {
			grid-template-columns: auto minmax(0, 1fr);
		}
	}
</style>
