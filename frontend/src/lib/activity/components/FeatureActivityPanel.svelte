<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { cancelJob } from '../client';
	import { getJobProgressStore } from '../context';
	import type { JobRecord } from '../store.svelte';
	import type { JobSnapshotResponse, ListJobsQuery } from '../types';
	import JobProgressCard from './JobProgressCard.svelte';

	let {
		scopeKey,
		query = {},
		queries,
		jobIds = [],
		heading = 'Active work',
		includeHistory = false,
		onSettled,
		active = $bindable(false),
		conflicting = $bindable(false)
	}: {
		scopeKey: string;
		query?: ListJobsQuery;
		queries?: ListJobsQuery[];
		jobIds?: string[];
		heading?: string;
		includeHistory?: boolean;
		onSettled?: (snapshot: JobSnapshotResponse) => void | Promise<void>;
		active?: boolean;
		conflicting?: boolean;
	} = $props();

	const store = getJobProgressStore();
	const notified = new SvelteSet<string>();
	const refreshedHistory = new SvelteSet<string>();
	const resolvedQueries = $derived(queries?.length ? queries : [query]);
	const queueScopeKeys = $derived(
		resolvedQueries.map((_, index) => (index === 0 ? scopeKey : `${scopeKey}:${index}`))
	);
	const historyScopeKeys = $derived(queueScopeKeys.map((key) => `${key}:history`));
	const records = $derived.by(() => {
		const byId = new SvelteMap<string, JobRecord>();
		for (const key of queueScopeKeys) {
			for (const record of store.recordsForScope(key)) byId.set(record.jobId, record);
		}
		if (includeHistory) {
			for (const key of historyScopeKeys) {
				for (const record of store.recordsForScope(key)) byId.set(record.jobId, record);
			}
		}
		for (const jobId of jobIds) {
			const record = store.records.get(jobId);
			if (record) byId.set(jobId, record);
		}
		return [...byId.values()].flatMap((record) =>
			record.row
				? [
						{
							jobId: record.jobId,
							row: record.row,
							snapshot: record.snapshot,
							freshness: record.freshness
						}
					]
				: []
		);
	});
	const activityState = $derived.by(() => {
		const activeJobIds = new SvelteSet<string>();
		for (const key of queueScopeKeys) {
			for (const jobId of store.activityForScope(key, jobIds).activeJobIds) {
				activeJobIds.add(jobId);
			}
		}
		return {
			active: activeJobIds.size > 0,
			conflicting: activeJobIds.size > 0,
			activeJobIds: [...activeJobIds]
		};
	});

	$effect(() => {
		active = activityState.active;
		conflicting = activityState.conflicting;
	});

	onMount(() => {
		const handles = resolvedQueries.map((item, index) =>
			store.acquireScope(queueScopeKeys[index], {
				...item,
				view: 'queue',
				limit: item.limit ?? 20
			})
		);
		const historyHandles = includeHistory
			? resolvedQueries.map((item, index) =>
					store.acquireScope(historyScopeKeys[index], {
						...item,
						view: 'history',
						limit: item.limit ?? 20
					})
				)
			: [];
		return () => {
			for (const handle of handles) handle.release();
			for (const handle of historyHandles) handle.release();
		};
	});

	$effect(() => {
		const bound = [...jobIds];
		untrack(() => {
			for (const jobId of bound) store.track(jobId);
			if (!bound.length) return;
			for (const key of queueScopeKeys) store.refreshScope(key);
			if (includeHistory) {
				for (const key of historyScopeKeys) store.refreshScope(key);
			}
		});
		return () => {
			untrack(() => {
				for (const jobId of bound) store.untrack(jobId);
			});
		};
	});

	$effect(() => {
		if (!includeHistory) return;
		for (const jobId of jobIds) {
			const snapshot = store.records.get(jobId)?.snapshot;
			if (snapshot?.phase !== 'terminal' || untrack(() => refreshedHistory.has(jobId))) continue;
			untrack(() => refreshedHistory.add(jobId));
			untrack(() => {
				for (const key of historyScopeKeys) store.refreshScope(key);
			});
		}
	});

	$effect(() => {
		if (!onSettled) return;
		for (const jobId of jobIds) {
			const snapshot = store.records.get(jobId)?.snapshot;
			if (snapshot?.phase !== 'terminal' || notified.has(jobId)) continue;
			notified.add(jobId);
			void onSettled(snapshot);
		}
	});

	async function requestCancel(jobId: string, expectedFenceToken: number) {
		const response = await cancelJob(fetch, jobId, expectedFenceToken);
		store.track(response.snapshot.job_id);
	}
</script>

{#if records.length}
	<section
		class="feature-activity"
		aria-label={heading}
		data-active={active}
		data-conflicting={conflicting}
	>
		<header>
			<h2>{heading}</h2>
			<a href="/projection-room">Open Activity</a>
		</header>
		<div class="cards">
			{#each records as record (record.jobId)}
				<JobProgressCard
					row={record.row}
					snapshot={record.snapshot}
					connection={store.connection}
					recordFreshness={record.freshness}
					onCancel={requestCancel}
				/>
			{/each}
		</div>
	</section>
{/if}

<style>
	.feature-activity,
	.cards {
		display: grid;
		gap: 10px;
	}
	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	h2 {
		margin: 0;
		font-size: 15px;
	}
	a {
		color: var(--gold);
		font-size: 12px;
	}
	@media (max-width: 520px) {
		header {
			align-items: start;
			flex-direction: column;
		}
	}
</style>
