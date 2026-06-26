<script lang="ts">
	import { onDestroy } from 'svelte';
	import RunProgress from './RunProgress.svelte';
	import type { JobListItem } from '$lib/api/jobs';
	import type { JobProgressDetail } from '$lib/jobs';
	import { durationH } from '$lib/display';
	import { displayJobLabel } from '$lib/job-labels';

	let {
		job,
		detail,
		status,
		onCancel
	}: {
		job: JobListItem;
		detail: JobProgressDetail;
		status: string;
		onCancel: () => void;
	} = $props();

	const title = $derived(job.subject?.title ?? displayJobLabel(job));
	const resourceKeys = $derived(Object.keys(job.resource_request ?? {}));

	// Tick once a second purely so the elapsed-time chip stays live between
	// progress events — those can be sparse during a long, quiet stage.
	let now = $state(Date.now());
	const timer = setInterval(() => (now = Date.now()), 1000);
	onDestroy(() => clearInterval(timer));

	const elapsedSeconds = $derived(
		job.started_at ? (now - new Date(job.started_at).getTime()) / 1000 : null
	);

	const childProgress = $derived(
		detail && typeof (detail as Record<string, unknown>).children_total === 'number'
			? (detail as unknown as {
					children_total: number;
					children_completed?: number;
					children_failed?: number;
				})
			: null
	);
</script>

<div class="card">
	<RunProgress {detail} {status} {title} onCancel={onCancel} />
	<div class="meta">
		<span class="chip type">{displayJobLabel(job)}</span>
		{#each resourceKeys as key (key)}
			<span class="chip resource">{key}</span>
		{/each}
		{#if elapsedSeconds != null}
			<span class="chip mono">{durationH(elapsedSeconds)} elapsed</span>
		{/if}
		<span class="chip mono">attempt {job.attempt_count}/{job.max_attempts}</span>
		{#if childProgress}
			<span class="chip children">
				{childProgress.children_completed ?? 0}/{childProgress.children_total} done
				{#if childProgress.children_failed}· {childProgress.children_failed} failed{/if}
			</span>
		{/if}
	</div>
</div>

<style>
	.card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip {
		padding: 2px 8px;
		border-radius: 99px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 11px;
		color: var(--muted);
	}
	.chip.type {
		color: var(--text);
	}
	.chip.resource {
		color: var(--gold);
		border-color: color-mix(in srgb, var(--gold) 30%, transparent);
	}
	.chip.children {
		color: var(--info);
		border-color: color-mix(in srgb, var(--info) 30%, transparent);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
