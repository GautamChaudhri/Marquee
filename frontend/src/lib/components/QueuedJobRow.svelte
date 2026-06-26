<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { JobListItem, ResourcePoolStatus } from '$lib/api/jobs';
	import { durationH } from '$lib/display';
	import { displayJobLabel } from '$lib/job-labels';
	import StatusDot from './StatusDot.svelte';

	let { job, resources }: { job: JobListItem; resources: ResourcePoolStatus[] } = $props();

	let now = $state(Date.now());
	const timer = setInterval(() => (now = Date.now()), 1000);
	onDestroy(() => clearInterval(timer));

	const waitingSeconds = $derived(
		(now - new Date(job.scheduled_at ?? job.created_at ?? now).getTime()) / 1000
	);

	/** Why is this job still queued? If it's blocked on a full resource pool,
	 *  say which one — both pieces of data (this job's requested resources,
	 *  and current pool capacity/in_use) are already fetched independently. */
	const waitingOn = $derived.by(() => {
		if (job.status !== 'waiting_resource') return null;
		const requested = Object.keys(job.resource_request ?? {});
		const full = resources.find((r) => requested.includes(r.key) && r.in_use >= r.capacity);
		return full ? `${full.key} (${full.in_use}/${full.capacity})` : null;
	});

	const statusTone = $derived(
		job.status === 'waiting_resource' ? 'warn' : job.status === 'paused' ? 'low' : 'info'
	);
</script>

<div class="row">
	<StatusDot tone={statusTone} />
	<div class="main">
		<span class="title">{job.subject?.title ?? displayJobLabel(job)}</span>
		<span class="type">{displayJobLabel(job)}</span>
	</div>
	<span class="status">{job.status.replace(/_/g, ' ')}</span>
	{#if waitingOn}
		<span class="chip waiting">waiting on {waitingOn}</span>
	{/if}
	<span class="chip mono">priority {job.priority}</span>
	<span class="chip mono">queued {durationH(waitingSeconds)}</span>
</div>

<style>
	.row {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 9px 12px;
		border-bottom: 1px solid var(--line2);
		flex-wrap: wrap;
	}
	.row:last-child {
		border-bottom: none;
	}
	.main {
		display: flex;
		flex-direction: column;
		min-width: 0;
		margin-right: auto;
	}
	.title {
		font-size: 13px;
		color: var(--text);
		font-weight: 550;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.type {
		font-size: 11px;
		color: var(--faint);
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
		white-space: nowrap;
	}
	.chip.waiting {
		color: var(--warn);
		border-color: color-mix(in srgb, var(--warn) 30%, transparent);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
