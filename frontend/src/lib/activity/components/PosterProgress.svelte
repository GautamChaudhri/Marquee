<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { listWorkItems } from '../client';
	import type { WorkItemPage, WorkItemRow, WorkItemSummary } from '../types';

	let {
		jobId,
		summary,
		expanded = false
	}: { jobId: string; summary: WorkItemSummary; expanded?: boolean } = $props();

	let open = $state(false);
	let items = $state<WorkItemRow[]>([]);
	let nextCursor = $state<number | null>(null);
	let displaySummary = $state<WorkItemSummary | null>(null);
	let loadedSequence = $state(-1);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let initialLoadStarted = $state(false);
	let controller: AbortController | null = null;

	// Mirrors the server's terminal set (core/jobs/work_items.py `_TERMINAL`). A row only
	// earns a colour once its status is final — colouring work in flight makes a running
	// chunk look like a finished one.
	const TERMINAL_STATUSES = new Set<WorkItemRow['status']>([
		'succeeded',
		'no_change',
		'review_required',
		'failed',
		'cancelled'
	]);
	const STATUS_LABELS: Record<WorkItemRow['status'], string> = {
		pending: 'Pending',
		running: 'Running',
		succeeded: 'Succeeded',
		no_change: 'No change',
		review_required: 'Ready for review',
		failed: 'Needs attention',
		cancelled: 'Cancelled'
	};
	type CountKey =
		'pending' | 'running' | 'succeeded' | 'no_change' | 'review_required' | 'failed' | 'cancelled';
	const COUNT_LABELS: Array<[CountKey, string]> = [
		['running', 'running'],
		['pending', 'pending'],
		['succeeded', 'succeeded'],
		['no_change', 'no change'],
		['review_required', 'ready for review'],
		['failed', 'need attention'],
		['cancelled', 'cancelled']
	];
	const currentSummary = $derived(displaySummary ?? summary);

	$effect(() => {
		if (displaySummary === null || summary.sequence > displaySummary.sequence) {
			displaySummary = summary;
		}
		if (open && items.length > 0 && summary.sequence > loadedSequence && !loading) {
			void refreshLoaded();
		}
	});
	$effect(() => {
		if (open && !initialLoadStarted) {
			initialLoadStarted = true;
			void refreshLoaded();
		}
	});

	onDestroy(() => controller?.abort());
	onMount(() => {
		if (expanded) open = true;
	});

	function subjectName(item: WorkItemRow): string {
		const subject = item.subject as Record<string, unknown>;
		const value = subject.display_name ?? subject.title ?? subject.series_title;
		return typeof value === 'string' && value ? value : item.subject_key;
	}

	function countEntries(): Array<[string, number, string]> {
		return COUNT_LABELS.flatMap(([key, label]) => {
			const count = currentSummary.counts?.[key] ?? 0;
			const displayLabel =
				key === 'failed'
					? count === 1
						? 'poster needs attention'
						: 'posters need attention'
					: label;
			return count > 0 ? [[key, count, displayLabel]] : [];
		});
	}

	async function fetchPages(target: number): Promise<void> {
		if (loading) return;
		loading = true;
		error = null;
		controller?.abort();
		controller = new AbortController();
		try {
			const refreshed: WorkItemRow[] = [];
			let cursor: number | undefined;
			let page: WorkItemPage;
			do {
				page = await listWorkItems(fetch, jobId, { cursor, limit: 50 }, controller.signal);
				refreshed.push(...page.items);
				cursor = page.next_cursor ?? undefined;
			} while (refreshed.length < target && cursor != null);
			items = refreshed;
			nextCursor = page.next_cursor ?? null;
			displaySummary = page.summary;
			loadedSequence = page.summary.sequence;
		} catch (cause) {
			if (!controller.signal.aborted) {
				error = cause instanceof Error ? cause.message : 'Poster progress could not be loaded.';
			}
		} finally {
			loading = false;
		}
	}

	async function loadMore(): Promise<void> {
		if (loading || nextCursor == null) return;
		loading = true;
		error = null;
		controller?.abort();
		controller = new AbortController();
		try {
			const page = await listWorkItems(
				fetch,
				jobId,
				{ cursor: nextCursor, limit: 50 },
				controller.signal
			);
			items = [...items, ...page.items];
			nextCursor = page.next_cursor ?? null;
			displaySummary = page.summary;
			loadedSequence = page.summary.sequence;
		} catch (cause) {
			if (!controller.signal.aborted) {
				error =
					cause instanceof Error ? cause.message : 'More poster progress could not be loaded.';
			}
		} finally {
			loading = false;
		}
	}

	function refreshLoaded(): Promise<void> {
		return fetchPages(Math.max(50, items.length));
	}
</script>

<details class="poster-progress" bind:open>
	<summary>
		<span>Poster progress</span>
		<span class="total">{currentSummary.total} posters</span>
	</summary>
	<div class="summary-counts" aria-label="Poster progress summary">
		{#each countEntries() as [key, count, label] (key)}
			<span data-status={key}><strong>{count}</strong> {label}</span>
		{/each}
	</div>
	<p class="legend">
		<strong>Succeeded</strong> — Marquee picked a poster.
		<strong>Ready for review</strong> — Marquee needs you to choose.
	</p>
	{#if error}
		<div class="load-error" role="alert">
			<span>{error}</span><button type="button" onclick={refreshLoaded}>Try again</button>
		</div>
	{/if}
	<div class="items" aria-busy={loading} aria-live="polite">
		{#each items as item (item.subject_key)}
			<!-- Stage, status word and bar all lived here once and were identical to the
			     card's own, on every row. This is a roster of what is in the chunk; the
			     colour carries the outcome and the card above carries the progress. -->
			<div
				class="item"
				class:settled={TERMINAL_STATUSES.has(item.status)}
				class:spans={item.status === 'failed' && Boolean(item.message)}
				data-status={item.status}
			>
				<strong>{subjectName(item)}</strong>
				<span class="sr-only">{STATUS_LABELS[item.status]}</span>
				{#if item.status === 'failed' && item.message}
					<p>{item.message}</p>
				{/if}
			</div>
		{/each}
		{#if loading && items.length === 0}<p class="empty">Loading poster progress…</p>{/if}
		{#if !loading && items.length === 0 && !error}<p class="empty">
				No poster rows are available.
			</p>{/if}
		{#if nextCursor != null}
			<button class="load-more" type="button" onclick={loadMore} disabled={loading}>
				{loading ? 'Loading…' : 'Load 50 more'}
			</button>
		{/if}
	</div>
</details>

<style>
	.poster-progress {
		min-width: 0;
		border-top: 1px solid var(--line);
		padding: 8px;
	}
	.poster-progress summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		cursor: pointer;
		color: var(--muted);
		font-size: 12px;
		font-weight: 700;
		list-style-position: inside;
	}
	.total {
		font-variant-numeric: tabular-nums;
		font-weight: 500;
	}
	.summary-counts {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin: 10px 0;
	}
	.summary-counts span {
		border: 1px solid var(--line);
		border-radius: 999px;
		padding: 4px 8px;
		color: var(--muted);
		font-size: 11px;
	}
	.summary-counts [data-status='failed'],
	.summary-counts [data-status='review_required'] {
		border-color: color-mix(in srgb, var(--warn) 45%, var(--line));
	}
	.items {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		align-content: start;
		gap: 7px;
		max-height: 420px;
		overflow: auto;
		overscroll-behavior: contain;
		padding-right: 4px;
	}
	.item {
		display: grid;
		min-width: 0;
		gap: 4px;
		border: 1px solid var(--line);
		border-left: 3px solid var(--line2);
		border-radius: 8px;
		padding: 9px 10px;
		background: var(--panel2);
		font-size: 12px;
	}
	.item strong {
		overflow-wrap: anywhere;
	}
	/* Colour is reserved for a settled outcome — see TERMINAL_STATUSES. */
	.item.settled[data-status='succeeded'],
	.item.settled[data-status='no_change'] {
		border-left-color: var(--good);
	}
	.item.settled[data-status='failed'] {
		border-left-color: var(--bad);
	}
	.item.settled[data-status='review_required'] {
		border-left-color: var(--warn);
	}
	.item.settled[data-status='cancelled'] {
		border-left-color: var(--muted);
	}
	.item.spans {
		grid-column: 1 / -1;
	}
	.item p,
	.empty {
		color: var(--muted);
		font-size: 11px;
	}
	.item p {
		margin: 0;
	}
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}
	.legend {
		margin: 0 0 10px;
		color: var(--faint2);
		font-size: 10.5px;
	}
	.legend strong {
		color: var(--muted);
		font-weight: 700;
	}
	.load-error {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin: 8px 0;
		border: 1px solid color-mix(in srgb, var(--bad) 45%, var(--line));
		border-radius: 8px;
		padding: 8px;
		font-size: 11px;
	}
	/* Both live inside the two-column .items grid and speak for the whole list. */
	.empty,
	.load-more {
		grid-column: 1 / -1;
		justify-self: center;
		margin: 4px;
	}
	@media (max-width: 560px) {
		.items {
			grid-template-columns: 1fr;
		}
	}
</style>
