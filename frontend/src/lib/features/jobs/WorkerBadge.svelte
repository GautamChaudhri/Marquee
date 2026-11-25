<script lang="ts">
	import { onMount } from 'svelte';
	import { api, call } from '#lib/api/client.ts';
	import type { WorkerInfo } from '#lib/api/types.ts';
	import { poll } from '#lib/poll.ts';

	const EVERY_MS = 15_000;

	let info = $state<WorkerInfo | null>(null);
	let failed = $state(false);

	onMount(() =>
		poll(async () => {
			const result = await call(api.GET('/api/worker'));
			failed = !result.ok;
			if (result.ok) info = result.data;
		}, EVERY_MS)
	);

	const label = $derived(
		failed
			? 'Worker unknown'
			: info === null
				? 'Worker…'
				: info.running
					? 'Worker running'
					: 'No worker'
	);
	const detail = $derived(
		info === null
			? ''
			: [
					info.running_jobs ? `${info.running_jobs} running` : '',
					info.queued ? `${info.queued} queued` : ''
				]
					.filter(Boolean)
					.join(', ')
	);
</script>

<a
	class="badge"
	href="/activity"
	title={detail ? `${label}: ${detail}` : label}
	data-testid="worker-badge"
>
	<span class="dot" class:on={info?.running && !failed} class:bad={failed}></span>
	<span class="label">{label}</span>
	{#if detail}<span class="detail">{detail}</span>{/if}
</a>

<style>
	.badge {
		display: flex;
		align-items: center;
		gap: 7px;
		height: 32px;
		padding: 0 10px;
		border-radius: var(--radius-pill);
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--muted);
		font-size: 12.5px;
		white-space: nowrap;
		min-width: 0;
	}
	.badge:hover {
		color: var(--text);
	}
	.dot {
		width: 8px;
		height: 8px;
		flex: none;
		border-radius: 50%;
		background: var(--faint);
	}
	.dot.on {
		background: var(--good);
	}
	.dot.bad {
		background: var(--bad);
	}
	.detail {
		color: var(--faint);
	}
	@media (max-width: 720px) {
		.label,
		.detail {
			display: none;
		}
		.badge {
			padding: 0 11px;
		}
	}
</style>
