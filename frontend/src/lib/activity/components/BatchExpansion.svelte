<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { Fetch } from '$lib/api/client';
	import { getBatchSummary, listChildren } from '../client';
	import type { BatchSummaryResponse, JobRow } from '../types';
	import JobProgressCard from './JobProgressCard.svelte';

	let { jobId }: { jobId: string } = $props();
	let summary = $state<BatchSummaryResponse | null>(null);
	let children = $state<JobRow[]>([]);
	let nextCursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let outcome = $state('');
	let controller: AbortController | null = null;
	const OUTCOME_LABELS: Record<string, string> = {
		succeeded: 'Succeeded',
		partially_succeeded: 'Partially succeeded',
		no_change: 'No change / not required',
		failed: 'Failed',
		cancelled: 'Cancelled',
		superseded: 'Superseded',
		unsafe: 'Unsafe',
		dead_letter: 'Needs operator review'
	};

	function scopedFetch(signal: AbortSignal): Fetch {
		return ((input: Parameters<Fetch>[0], init?: RequestInit) =>
			fetch(input, {
				...init,
				signal: init?.signal ? AbortSignal.any([init.signal, signal]) : signal
			})) as Fetch;
	}

	async function load(cursor: string | null = null): Promise<void> {
		if (loading) return;
		controller?.abort();
		controller = new AbortController();
		loading = true;
		error = null;
		try {
			const boundedFetch = scopedFetch(controller.signal);
			const [batch, page] = await Promise.all([
				summary ? Promise.resolve(summary) : getBatchSummary(boundedFetch, jobId),
				listChildren(boundedFetch, jobId, {
					cursor: cursor ?? undefined,
					limit: 20,
					sort: 'failed_first',
					outcome: outcome || undefined
				})
			]);
			summary = batch;
			children = cursor ? [...children, ...page.items] : page.items;
			nextCursor = page.next_cursor;
		} catch (reason) {
			if (!controller.signal.aborted) {
				error = reason instanceof Error ? reason.message : 'Batch details are unavailable.';
			}
		} finally {
			loading = false;
		}
	}

	function toggle(event: Event): void {
		const details = event.currentTarget as HTMLDetailsElement;
		if (details.open && !summary) void load();
		if (!details.open) controller?.abort();
	}

	function filterChildren(event: Event): void {
		outcome = (event.currentTarget as HTMLSelectElement).value;
		children = [];
		nextCursor = null;
		controller?.abort();
		loading = false;
		void load();
	}

	onDestroy(() => controller?.abort());
</script>

<details ontoggle={toggle}>
	<summary>Batch details</summary>
	<div class="body">
		{#if summary}
			<div class="counts" aria-label="Batch outcome summary">
				<span>{summary.terminal_total} finished</span>
				<span>{summary.created_total} created</span>
				<span
					>{summary.sealed
						? `${summary.sealed_child_total ?? 0} sealed`
						: 'Still discovering work'}</span
				>
				{#each Object.entries(summary.outcomes) as [outcome, count] (outcome)}
					<span>{OUTCOME_LABELS[outcome] ?? 'Other'}: {count}</span>
				{/each}
			</div>
		{/if}
		{#if error}<p class="error" role="status">{error}</p>{/if}
		<label class="filter">
			<span>Child outcome</span>
			<select value={outcome} onchange={filterChildren}>
				<option value="">Failed and attention outcomes first</option>
				<option value="failed">Failed only</option>
				<option value="partially_succeeded">Partially succeeded only</option>
				<option value="unsafe">Unsafe only</option>
				<option value="dead_letter">Operator review only</option>
			</select>
		</label>
		{#if children.length}
			<div class="children">
				{#each children as child (child.job_id)}
					<JobProgressCard row={child} />
				{/each}
			</div>
		{:else if !loading && !error}
			<p class="empty">No child work is available.</p>
		{/if}
		{#if nextCursor}
			<button type="button" onclick={() => load(nextCursor)} disabled={loading}
				>Load more children</button
			>
		{:else if loading}
			<p class="empty" aria-live="polite">Loading bounded batch details…</p>
		{/if}
	</div>
</details>

<style>
	details {
		border-top: 1px solid var(--line);
		padding-top: 9px;
	}
	summary {
		color: var(--muted);
		cursor: pointer;
		font-size: 12px;
	}
	.body {
		display: grid;
		gap: 10px;
		padding-top: 10px;
	}
	.counts {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.counts span {
		padding: 4px 7px;
		border: 1px solid var(--line);
		border-radius: 99px;
		color: var(--muted);
		font-size: 11px;
	}
	.children {
		display: grid;
		gap: 8px;
		max-height: 560px;
		overflow: auto;
		overscroll-behavior: contain;
	}
	.children :global(.card) {
		content-visibility: auto;
		contain-intrinsic-size: auto 240px;
	}
	.filter {
		display: flex;
		align-items: center;
		gap: 8px;
		color: var(--muted);
		font-size: 11px;
	}
	.filter select {
		min-height: 32px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
	}
	.empty,
	.error {
		margin: 0;
		color: var(--muted);
		font-size: 12px;
	}
	.error {
		color: var(--bad);
	}
	button {
		justify-self: start;
		padding: 6px 9px;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
	}
</style>
