<script lang="ts">
	import { untrack } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import SubtitleMovieList from '$lib/components/subtitles/SubtitleMovieList.svelte';
	import { scanLibrarySubtitles } from '$lib/api/subtitles';
	import { toast } from '$lib/toast';

	let { data } = $props();

	let movies = $state(untrack(() => data.movies?.items || []));
	let scanningLibrary = $state(false);
	let initiatedJobIds = $state<string[]>([]);

	$effect(() => {
		if (data.movies?.items) {
			movies = data.movies.items;
		}
	});

	async function triggerLibraryScan() {
		scanningLibrary = true;
		try {
			const job = await scanLibrarySubtitles(fetch, false);
			if (!initiatedJobIds.includes(job.job_id)) {
				initiatedJobIds = [...initiatedJobIds, job.job_id];
			}
			toast('Library subtitle scan job enqueued', 'good');
		} catch (e: unknown) {
			toast(e instanceof Error ? e.message : 'Failed to enqueue library scan job', 'bad');
		} finally {
			scanningLibrary = false;
		}
	}
</script>

<svelte:head>
	<title>Movies Subtitles – Marquee</title>
</svelte:head>

{#snippet pageActions()}
	<button class="btn-scan" onclick={triggerLibraryScan} disabled={scanningLibrary}>
		🔍 {scanningLibrary ? 'Queued...' : 'Scan Library for Subtitles'}
	</button>
{/snippet}

<div class="page-container">
	<SectionHeader
		title="Movie Subtitle Inventory"
		subtitle="Manage container embedded and external audio and subtitle tracks for films."
		action={pageActions}
	/>
	<FeatureActivityPanel
		scopeKey="feature:audio-subtitles:movies"
		query={{ feature_area: 'audio_subtitles', subject_kind: 'movie' }}
		jobIds={initiatedJobIds}
		heading="Movie subtitle activity"
		onSettled={() => invalidateAll()}
	/>

	{#if data.error}
		<div class="error-banner">
			<p>Failed to load subtitles page: {data.error}</p>
		</div>
	{/if}

	<div class="movies-content-wrapper">
		{#if movies}
			<SubtitleMovieList {movies} initialFilter={data.status || 'all'} />
		{:else}
			<div class="loading-state">Loading inventory...</div>
		{/if}
	</div>
</div>

<style>
	.page-container {
		display: flex;
		flex-direction: column;
		gap: 18px;
		height: 100%;
		padding-bottom: 24px;
	}
	.movies-content-wrapper {
		flex: 1;
		min-height: 0;
	}
	.loading-state {
		text-align: center;
		padding: 48px;
		color: var(--muted);
	}
	.error-banner {
		padding: 14px;
		background: rgba(239, 83, 80, 0.1);
		border: 1px solid var(--bad);
		color: var(--bad);
		border-radius: var(--radius-sm);
		font-size: 13.5px;
	}
	.btn-scan {
		font-size: 13px;
		font-weight: 550;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
		transition:
			background-color 0.15s,
			border-color 0.15s,
			color 0.15s;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		outline: none;
	}
	.btn-scan:hover:not(:disabled) {
		background: var(--panel);
		border-color: var(--gold);
		color: var(--gold);
	}
	.btn-scan:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
