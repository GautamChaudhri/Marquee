<script lang="ts">
	import { browser } from '$app/environment';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import SubtitleMovieList from '$lib/components/subtitles/SubtitleMovieList.svelte';
	import SubgenStatus from '$lib/components/subtitles/SubgenStatus.svelte';
	import SubgenPanel from '$lib/components/subtitles/SubgenPanel.svelte';
	import PolicyList from '$lib/components/subtitles/PolicyList.svelte';
	import PolicyEditor from '$lib/components/subtitles/PolicyEditor.svelte';
	import JobList from '$lib/components/subtitles/JobList.svelte';
	import type { SubtitlePolicy } from '$lib/api/types';
	import { scanLibrarySubtitles } from '$lib/api/subtitles';
	import { listMovies } from '$lib/api/library';
	import { toast } from '$lib/toast';

	let { data } = $props();

	let tabs = [
		{ id: 'inventory', label: 'Inventory' },
		{ id: 'generation', label: 'AI Generation' },
		{ id: 'policies', label: 'Policies' },
		{ id: 'jobs', label: 'Jobs Queue' }
	];

	let activeTab = $state('inventory');

	// Local reactive state for movies
	let movies = $state(data.movies?.items || []);
	let scanningLibrary = $state(false);

	$effect(() => {
		if (data.movies?.items) {
			movies = data.movies.items;
		}
	});

	async function refreshMovies() {
		try {
			const res = await listMovies(fetch, { page_size: 200 });
			movies = res.items;
		} catch (e) {
			console.error(e);
		}
	}

	async function triggerLibraryScan() {
		scanningLibrary = true;
		try {
			await scanLibrarySubtitles(fetch, false);
			toast('Library subtitle scan job enqueued', 'good');
		} catch (e: any) {
			toast(e.message || 'Failed to enqueue library scan job', 'bad');
		} finally {
			scanningLibrary = false;
		}
	}

	// Persist active tab selection
	$effect(() => {
		if (browser) {
			const cached = localStorage.getItem('marquee:active_subtitles_tab');
			if (cached && tabs.some((t) => t.id === cached)) {
				activeTab = cached;
			}
		}
	});

	function handleTabSelect(id: string) {
		activeTab = id;
		if (browser) {
			localStorage.setItem('marquee:active_subtitles_tab', id);
		}
	}

	// Policy editor navigation state
	let activePolicy = $state<SubtitlePolicy | null>(null);
	let editorOpen = $state(false);

	function openPolicyEditor(policy: SubtitlePolicy) {
		activePolicy = policy;
		editorOpen = true;
	}

	function closePolicyEditor() {
		activePolicy = null;
		editorOpen = false;
	}
</script>

<svelte:head>
	<title>Subtitles – Marquee</title>
</svelte:head>

{#snippet pageActions()}
	<button class="btn-scan" onclick={triggerLibraryScan} disabled={scanningLibrary}>
		🔍 {scanningLibrary ? 'Queued...' : 'Scan Library for Subtitles'}
	</button>
{/snippet}

<div class="page-container">
	<SectionHeader title="Subtitle Management" subtitle="Manage container embedded and external subtitle tracks across your library." action={pageActions} />

	{#if data.error}
		<div class="error-banner">
			<p>Failed to load subtitles page: {data.error}</p>
		</div>
	{/if}

	<div class="tabs-row">
		<TabBar {tabs} active={activeTab} onSelect={handleTabSelect} />
	</div>

	<div class="tab-content-wrapper">
		{#if activeTab === 'inventory'}
			{#if movies}
				<SubtitleMovieList {movies} />
			{:else}
				<div class="loading-state">Loading inventory...</div>
			{/if}
		{:else if activeTab === 'generation'}
			<div class="generation-grid">
				<SubgenStatus />
				<SubgenPanel />
			</div>
		{:else if activeTab === 'policies'}
			{#if editorOpen}
				<PolicyEditor
					policy={activePolicy || ({} as any)}
					onSave={closePolicyEditor}
					onCancel={closePolicyEditor}
				/>
			{:else}
				<PolicyList
					onEdit={openPolicyEditor}
					onAudit={openPolicyEditor}
					onApply={openPolicyEditor}
				/>
			{/if}
		{:else if activeTab === 'jobs'}
			<JobList />
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
	.tabs-row {
		border-bottom: 1px solid var(--line);
		padding-bottom: 6px;
	}
	.tab-content-wrapper {
		flex: 1;
		min-height: 0;
	}
	.generation-grid {
		display: grid;
		grid-template-columns: 320px 1fr;
		gap: 20px;
		align-items: flex-start;
	}
	@media (max-width: 800px) {
		.generation-grid {
			grid-template-columns: 1fr;
		}
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
		transition: background-color 0.15s, border-color 0.15s, color 0.15s;
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
