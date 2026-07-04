<!-- eslint-disable @typescript-eslint/no-explicit-any -->
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
	import { putSettings } from '$lib/api/system';
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
	// svelte-ignore state_referenced_locally
	let movies = $state(data.movies?.items || []);
	// svelte-ignore state_referenced_locally
	let settings = $state(data.settings);
	let scanningLibrary = $state(false);
	let savingPreferences = $state(false);
	let preferredShared = $state('en');
	let preferredAudio = $state('en');
	let preferredSubtitles = $state('en');
	let separatePreferred = $state(false);
	let preferencesInitialized = $state(false);

	$effect(() => {
		if (data.movies?.items) {
			movies = data.movies.items;
		}
	});

	$effect(() => {
		if (settings && !preferencesInitialized) {
			const sub = settings.subtitles || {};
			preferredShared = (sub.preferred_languages || ['en']).join(', ');
			preferredAudio = (
				sub.preferred_audio_languages ||
				sub.effective_preferred_audio_languages ||
				sub.preferred_languages || ['en']
			).join(', ');
			preferredSubtitles = (
				sub.preferred_subtitle_languages ||
				sub.effective_preferred_subtitle_languages ||
				sub.preferred_languages || ['en']
			).join(', ');
			separatePreferred = Boolean(
				sub.preferred_audio_languages || sub.preferred_subtitle_languages
			);
			preferencesInitialized = true;
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

	function parseLanguages(value: string) {
		return value
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);
	}

	async function savePreferredLanguages() {
		savingPreferences = true;
		try {
			const payload = {
				subtitles: {
					preferred_languages: parseLanguages(preferredShared),
					preferred_audio_languages: separatePreferred ? parseLanguages(preferredAudio) : null,
					preferred_subtitle_languages: separatePreferred
						? parseLanguages(preferredSubtitles)
						: null
				}
			};
			const response = await putSettings(fetch, payload);
			settings = response.settings;
			preferencesInitialized = false;
			await refreshMovies();
			toast('Preferred languages saved', 'good');
		} catch (e: any) {
			toast(e.message || 'Failed to save preferred languages', 'bad');
		} finally {
			savingPreferences = false;
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
	<title>Audio & Subs – Marquee</title>
</svelte:head>

{#snippet pageActions()}
	<button class="btn-scan" onclick={triggerLibraryScan} disabled={scanningLibrary}>
		🔍 {scanningLibrary ? 'Queued...' : 'Scan Library for Subtitles'}
	</button>
{/snippet}

<div class="page-container">
	<SectionHeader
		title="Audio and Subtitle Management"
		subtitle="Manage container embedded and external audio and subtitle tracks across your library."
		action={pageActions}
	/>

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
			<div class="preferences-panel mq-rise">
				<div class="preferences-head">
					<div>
						<h3>Preferred Languages</h3>
						<p>Used for audio/subtitle gap status across the library.</p>
					</div>
					<label class="toggle-row">
						<input type="checkbox" bind:checked={separatePreferred} />
						<span>Separate audio and subtitles</span>
					</label>
				</div>
				<div class="preferences-grid" class:split={separatePreferred}>
					<label class="setting-field">
						<span>{separatePreferred ? 'Shared fallback' : 'Audio & subtitles'}</span>
						<input
							type="text"
							bind:value={preferredShared}
							placeholder="en, es, fr"
							autocomplete="off"
						/>
					</label>
					{#if separatePreferred}
						<label class="setting-field">
							<span>Audio</span>
							<input
								type="text"
								bind:value={preferredAudio}
								placeholder="en, es"
								autocomplete="off"
							/>
						</label>
						<label class="setting-field">
							<span>Subtitles</span>
							<input
								type="text"
								bind:value={preferredSubtitles}
								placeholder="en, fr"
								autocomplete="off"
							/>
						</label>
					{/if}
					<button
						class="btn-save-preferences"
						onclick={savePreferredLanguages}
						disabled={savingPreferences}
					>
						{savingPreferences ? 'Saving...' : 'Save'}
					</button>
				</div>
			</div>
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
	.preferences-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		margin-bottom: 16px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.preferences-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
	}
	.preferences-head h3 {
		margin: 0;
		font-size: 15px;
	}
	.preferences-head p {
		margin: 4px 0 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.toggle-row {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		color: var(--muted);
		font-size: 12.5px;
		cursor: pointer;
		white-space: nowrap;
	}
	.toggle-row input[type='checkbox'] {
		appearance: none;
		width: 16px;
		height: 16px;
		border-radius: 999px;
		border: 1px solid var(--line2);
		background: var(--ink2);
		cursor: pointer;
		box-shadow: inset 0 0 0 4px var(--ink2);
		transition:
			border-color 0.15s,
			background-color 0.15s,
			box-shadow 0.15s;
	}
	.toggle-row input[type='checkbox']:checked {
		border-color: var(--gold);
		background: var(--gold);
		box-shadow:
			0 0 0 3px var(--gold-soft),
			0 0 14px rgba(255, 190, 73, 0.35);
	}
	.preferences-grid {
		display: grid;
		grid-template-columns: minmax(220px, 1fr) auto;
		gap: 12px;
		align-items: end;
	}
	.preferences-grid.split {
		grid-template-columns: repeat(3, minmax(160px, 1fr)) auto;
	}
	.setting-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}
	.setting-field span {
		font-size: 11px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.setting-field input {
		width: 100%;
		padding: 9px 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13px;
		outline: none;
	}
	.setting-field input:focus {
		border-color: var(--gold);
		box-shadow: 0 0 0 2px var(--gold-soft);
	}
	.btn-save-preferences {
		font-size: 13px;
		font-weight: 650;
		padding: 9px 16px;
		border-radius: var(--radius-sm);
		border: 1px solid color-mix(in srgb, var(--gold) 60%, transparent);
		background: var(--gold);
		color: var(--ink);
		cursor: pointer;
	}
	.btn-save-preferences:disabled {
		opacity: 0.6;
		cursor: not-allowed;
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
		.preferences-head {
			align-items: flex-start;
			flex-direction: column;
		}
		.preferences-grid,
		.preferences-grid.split {
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
