<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { cancelJob } from '../client';
	import { getJobProgressStore } from '../context';
	import type { JobSnapshotResponse, ListJobsQuery } from '../types';
	import JobProgressCard from './JobProgressCard.svelte';

	let {
		scopeKey,
		query,
		jobIds = [],
		heading = 'Active work',
		includeHistory = false,
		onSettled,
		active = $bindable(false),
		conflicting = $bindable(false)
	}: {
		scopeKey: string;
		query: ListJobsQuery;
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
	let historyScopeKey = $derived(`${scopeKey}:history`);
	const records = $derived.by(() => {
		const byId = new SvelteMap(
			store.recordsForScope(scopeKey).map((record) => [record.jobId, record])
		);
		if (includeHistory) {
			for (const record of store.recordsForScope(historyScopeKey)) {
				byId.set(record.jobId, record);
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
	const activityState = $derived(store.activityForScope(scopeKey, jobIds));

	$effect(() => {
		active = activityState.active;
		conflicting = activityState.conflicting;
	});

	onMount(() => {
		const handle = store.acquireScope(scopeKey, {
			...query,
			view: 'queue',
			limit: query.limit ?? 20
		});
		const historyHandle = includeHistory
			? store.acquireScope(historyScopeKey, {
					...query,
					view: 'history',
					limit: query.limit ?? 20
				})
			: null;
		return () => {
			handle.release();
			historyHandle?.release();
		};
	});

	$effect(() => {
		const bound = [...jobIds];
		untrack(() => {
			for (const jobId of bound) store.track(jobId);
			if (!bound.length) return;
			store.refreshScope(scopeKey);
			if (includeHistory) store.refreshScope(historyScopeKey);
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
			untrack(() => store.refreshScope(historyScopeKey));
		}
	});

	$effect(() => {
		if (!onSettled) return;
		for (const jobId of new Set([...records.map((record) => record.jobId), ...jobIds])) {
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
