<script lang="ts">
	import { onDestroy } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { getJobProgressStore } from '../context';
	import { listContainedWork } from '../client';
	import type {
		ContainedWorkItem,
		ContainedWorkPage,
		ContainedWorkSummary,
		JobSnapshotResponse
	} from '../types';

	let {
		jobId,
		summary,
		open = $bindable(false),
		id = undefined
	}: {
		jobId: string;
		summary: ContainedWorkSummary;
		open?: boolean;
		id?: string;
	} = $props();

	let items = $state<ContainedWorkItem[]>([]);
	let nextCursor = $state<string | null>(null);
	let loadedSequence = $state(-1);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let initialLoadStarted = $state(false);
	let controller: AbortController | null = null;
	const tracked = new SvelteSet<string>();
	let store: ReturnType<typeof getJobProgressStore> | null = null;
	try {
		store = getJobProgressStore();
	} catch {
		// Standalone component tests do not install the page-level Activity context.
	}

	$effect(() => {
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
	$effect(() => {
		const wanted = new Set(
			open && summary.source === 'child_jobs'
				? items.filter((item) => item.detail_href).map((item) => item.key)
				: []
		);
		for (const jobId of wanted) {
			if (!tracked.has(jobId)) {
				store?.track(jobId);
				tracked.add(jobId);
			}
		}
		for (const jobId of [...tracked]) {
			if (!wanted.has(jobId)) {
				store?.untrack(jobId);
				tracked.delete(jobId);
			}
		}
	});

	onDestroy(() => {
		controller?.abort();
		for (const jobId of tracked) store?.untrack(jobId);
	});

	function subjectName(item: ContainedWorkItem): string {
		const value = item.subject.display_name ?? item.subject.title ?? item.subject.series_title;
		return typeof value === 'string' && value ? value : item.key;
	}

	function liveSnapshot(item: ContainedWorkItem): JobSnapshotResponse | null {
		return item.detail_href ? (store?.records.get(item.key)?.snapshot ?? null) : null;
	}

	function liveState(item: ContainedWorkItem): ContainedWorkItem['status'] {
		const snapshot = liveSnapshot(item);
		if (!snapshot) return item.status;
		if (snapshot.phase !== 'terminal') {
			if (snapshot.progress?.wait?.kind === 'retry') return 'retrying';
			return snapshot.phase === 'running' || snapshot.phase === 'stopping' ? 'running' : 'pending';
		}
		if (snapshot.outcome === 'succeeded') return 'succeeded';
		if (snapshot.outcome === 'no_change') return 'no_change';
		if (snapshot.outcome === 'partially_succeeded') return 'review_required';
		if (snapshot.outcome === 'cancelled' || snapshot.outcome === 'superseded') return 'cancelled';
		return 'failed';
	}

	function statusLabel(item: ContainedWorkItem): string {
		return liveSnapshot(item)?.status.label ?? item.status_label;
	}

	function stageName(item: ContainedWorkItem): string | null {
		const progress = liveSnapshot(item)?.progress;
		return progress?.stage_label ?? progress?.headline ?? item.stage_name ?? null;
	}

	function message(item: ContainedWorkItem): string | null {
		return liveSnapshot(item)?.attention.message ?? item.message ?? null;
	}

	/** Position, stage, and how much work this subject brought in, as one line.
	 *  Assembled here rather than in markup because the separators only read
	 *  correctly when the surrounding whitespace is not at the mercy of block
	 *  boundaries. */
	function stageLine(item: ContainedWorkItem): string | null {
		const stage = stageName(item);
		if (!stage) return null;
		const parts: string[] = [];
		if (item.stage_number && item.stage_total)
			parts.push(`Stage ${item.stage_number} of ${item.stage_total}`);
		parts.push(stage);
		if (item.source_count != null) parts.push(`${item.source_count} posters found`);
		return parts.join(' · ');
	}

	function measurement(item: ContainedWorkItem) {
		const progress = liveSnapshot(item)?.progress;
		return progress?.current ?? progress?.overall ?? item.progress ?? null;
	}

	async function fetchPages(target: number): Promise<void> {
		if (loading) return;
		loading = true;
		error = null;
		controller?.abort();
		controller = new AbortController();
		try {
			const refreshed: ContainedWorkItem[] = [];
			let cursor: string | undefined;
			let page: ContainedWorkPage;
			do {
				page = await listContainedWork(fetch, jobId, { cursor, limit: 50 }, controller.signal);
				refreshed.push(...page.items);
				cursor = page.next_cursor ?? undefined;
			} while (refreshed.length < target && cursor != null);
			items = refreshed;
			nextCursor = page.next_cursor ?? null;
			loadedSequence = page.summary.sequence;
		} catch (cause) {
			if (!controller.signal.aborted) {
				error = cause instanceof Error ? cause.message : 'Contained work could not be loaded.';
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
			const page = await listContainedWork(
				fetch,
				jobId,
				{ cursor: nextCursor, limit: 50 },
				controller.signal
			);
			items = [...items, ...page.items];
			nextCursor = page.next_cursor ?? null;
			loadedSequence = page.summary.sequence;
		} catch (cause) {
			if (!controller.signal.aborted) {
				error = cause instanceof Error ? cause.message : 'More contained work could not be loaded.';
			}
		} finally {
			loading = false;
		}
	}

	function refreshLoaded(): Promise<void> {
		return fetchPages(Math.max(50, items.length));
	}
</script>

<div class="contained-work" {id} hidden={!open}>
	{#if error}
		<div class="load-error" role="alert">
			<span>{error}</span><button type="button" onclick={refreshLoaded}>Try again</button>
		</div>
	{/if}
	<div class="items" aria-busy={loading} aria-live="polite">
		{#each items as item (item.key)}
			{@const state = liveState(item)}
			{@const currentMeasurement = measurement(item)}
			{@const stage = stageLine(item)}
			<div class="item" data-status={state}>
				<div class="item-heading">
					<strong>{subjectName(item)}</strong>
					<span class="item-status">{statusLabel(item)}</span>
				</div>
					{#if stage}<span class="stage">{stage}</span>{/if}
				{#if currentMeasurement?.completed != null && currentMeasurement?.total != null}
					<progress max={currentMeasurement.total} value={currentMeasurement.completed}></progress>
					<span class="measure"
						>{currentMeasurement.completed} / {currentMeasurement.total}{#if currentMeasurement.unit}
							{` ${currentMeasurement.unit}`}{/if}</span
					>
				{/if}
				{#if (state === 'failed' || state === 'review_required') && message(item)}
					<p>{message(item)}</p>
				{/if}
				{#if item.detail_href}<a class="child-detail" href={item.detail_href}>Details</a>{/if}
			</div>
		{/each}
		{#if loading && items.length === 0}<p class="empty">Loading contained work…</p>{/if}
		{#if !loading && items.length === 0 && !error}<p class="empty">
				No contained work is available.
			</p>{/if}
		{#if nextCursor != null}
			<button class="load-more" type="button" onclick={loadMore} disabled={loading}>
				{loading ? 'Loading…' : 'Load 50 more'}
			</button>
		{/if}
	</div>
</div>

<style>
	.contained-work {
		min-width: 0;
		padding: 0 8px 8px;
	}
	.contained-work[hidden] {
		display: none;
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
		gap: 5px;
		border: 1px solid var(--line);
		border-left: 3px solid var(--line2);
		border-radius: 8px;
		padding: 9px 10px;
		background: var(--panel2);
		font-size: 12px;
	}
	.item[data-status='running'] {
		border-left-color: var(--info);
	}
	.item[data-status='retrying'],
	.item[data-status='review_required'] {
		border-left-color: var(--warn);
	}
	.item[data-status='succeeded'] {
		border-left-color: var(--good);
	}
	.item[data-status='failed'] {
		border-left-color: var(--bad);
	}
	.item-heading {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 8px;
	}
	.item-heading strong {
		overflow-wrap: anywhere;
	}
	.item-status,
	.stage,
	.measure,
	.item p {
		color: var(--muted);
		font-size: 11px;
	}
	.item-status {
		flex: none;
	}
	.item p {
		margin: 0;
	}
	progress {
		width: 100%;
		height: 4px;
		accent-color: var(--info);
	}
	.child-detail {
		justify-self: start;
		color: var(--muted);
		font-size: 11px;
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
