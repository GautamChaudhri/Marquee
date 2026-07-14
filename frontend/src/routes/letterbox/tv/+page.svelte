<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from '$lib/toast';
	import { detectLetterboxTv, detectLetterboxTvLibrary } from '$lib/api/letterbox';
	import { cancelJob, type JobSnapshot } from '$lib/api/jobs';
	import { trackJob } from '$lib/jobs';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import SegmentedBar from '$lib/components/SegmentedBar.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import type { LetterboxTvShowRollup } from '$lib/api/types';
	import { LETTERBOX_TV_CONTENT_META, LETTERBOX_TV_VERDICT_META } from '$lib/letterbox/status-meta';

	let { data } = $props();

	let items = $derived(data.tvData?.items || []);
	let filters = $derived(data.filters);

	// Filters local state
	let searchQuery = $state(filters.q || '');
	let filterVerdict = $state(filters.verdict || '');
	let filterUniformity = $state(filters.uniformity || '');
	let filterHasCandidates = $state(
		filters.has_candidates === true ? 'true' : filters.has_candidates === false ? 'false' : ''
	);

	// Selection
	let selectedIds = $state<Record<number, boolean>>({});
	const allSelected = $derived(items.length > 0 && items.every((i) => selectedIds[i.series_id]));
	const someSelected = $derived(items.some((i) => selectedIds[i.series_id]) && !allSelected);

	function toggleSelectAll() {
		if (allSelected) {
			selectedIds = {};
		} else {
			const next: Record<number, boolean> = {};
			for (const item of items) {
				next[item.series_id] = true;
			}
			selectedIds = next;
		}
	}

	function toggleSelect(id: number) {
		selectedIds[id] = !selectedIds[id];
	}

	// Batch Detection State
	let detecting = $state(false);
	let exhaustive = $state(false);
	let activeJobId = $state<string | null>(null);
	let progress = $state(0);
	let progressDone = $state(0);
	let progressTotal = $state(0);
	let jobStatus = $state<string | null>(null);
	let trackStop = $state<(() => void) | null>(null);

	function updateFilters() {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		if (searchQuery) sp.set('q', searchQuery);
		else sp.delete('q');

		if (filterVerdict) sp.set('verdict', filterVerdict);
		else sp.delete('verdict');

		if (filterUniformity) sp.set('uniformity', filterUniformity);
		else sp.delete('uniformity');

		if (filterHasCandidates) sp.set('has_candidates', filterHasCandidates);
		else sp.delete('has_candidates');

		// reset page or similar if needed
		goto(`/letterbox/tv?${sp.toString()}`, { keepFocus: true, noScroll: true });
	}

	function clearFilters() {
		searchQuery = '';
		filterVerdict = '';
		filterUniformity = '';
		filterHasCandidates = '';
		goto('/letterbox/tv');
	}

	function segmentsForRollup(rollup: LetterboxTvShowRollup) {
		const counts = rollup.bucket_counts || {};
		return [
			{
				key: 'widescreen',
				count: counts.widescreen || 0,
				tone: 'good' as const
			},
			{
				key: 'sampled_widescreen',
				count: counts.sampled_widescreen || 0,
				tone: 'sampled_widescreen' as const
			},
			{ key: 'candidate', count: counts.candidate || 0, tone: 'warn' as const },
			{ key: 'tagged', count: counts.tagged || 0, tone: 'info' as const },
			{ key: 'reencoded', count: counts.reencoded || 0, tone: 'gold' as const },
			{ key: 'variable', count: counts.variable || 0, tone: 'dovi' as const },
			{ key: 'open_matte', count: counts.open_matte || 0, tone: 'teal' as const },
			{ key: 'pillarbox', count: counts.pillarbox || 0, tone: 'magenta' as const },
			{ key: 'error', count: counts.error || 0, tone: 'bad' as const },
			{ key: 'ineligible', count: counts.ineligible || 0, tone: 'muted' as const },
			{ key: 'unanalyzed', count: counts.unanalyzed || 0, tone: 'muted' as const }
		];
	}

	function getClearFraction(rollup: LetterboxTvShowRollup): string {
		const counts = rollup.bucket_counts || {};
		const content =
			(counts.widescreen || 0) +
			(counts.sampled_widescreen || 0) +
			(counts.open_matte || 0) +
			(counts.pillarbox || 0);
		return `${content}/${rollup.episodes_total}`;
	}

	async function runBatch() {
		const selected = Object.entries(selectedIds)
			.filter((entry) => entry[1])
			.map((entry) => Number(entry[0]));

		if (detecting) return;
		detecting = true;
		progress = 0;
		progressDone = 0;
		progressTotal = 0;

		try {
			let ref;
			if (selected.length > 0) {
				// Detect selected shows one by one (or enqueued in a batch via backend if we can)
				// Wait! The backend detect_tv_series endpoint is POST /api/letterbox/tv/{series_id}/detect
				// We can run detection sequentially or just run a batch on the first selected and notify.
				// Wait! The plan C6 says "batch progress chips per season while running" or "Analyze show with Exhaustive + Force".
				// Let's run detect on the selected shows. If there's multiple, let's run them.
				// To make it simple and robust, if they select specific shows, let's run detect on each!
				toast(`Starting detection for ${selected.length} selected shows...`, 'good');
				for (const id of selected) {
					await detectLetterboxTv(fetch, id, { exhaustive });
				}
				toast('Selected shows enqueued for detection!', 'good');
				detecting = false;
				selectedIds = {};
			} else {
				// Detect all
				ref = await detectLetterboxTvLibrary(fetch, { exhaustive });
				activeJobId = ref.job_id;
				toast(`Started library TV detection (${exhaustive ? 'exhaustive' : 'triage'})...`, 'good');
				rehydrateJob(ref.job_id);
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to start TV detection', 'bad');
			detecting = false;
		}
	}

	function rehydrateJob(jobId: string) {
		detecting = true;
		jobStatus = 'running';

		trackStop?.();
		trackStop = trackJob<JobSnapshot>(
			fetch,
			jobId,
			{
				onProgress: (p) => {
					jobStatus = p.status;
					const prg = p.detail || {};
					progressDone = Number(prg.children_completed || prg.done || 0);
					progressTotal = Number(prg.children_total || prg.total || 0);
					progress = progressTotal > 0 ? (progressDone / progressTotal) * 100 : 0;
				},
				onDone: (job) => {
					detecting = false;
					activeJobId = null;
					jobStatus = job.status;
					toast('TV Letterbox detection completed successfully!', 'good');
					// reload page
					goto(page.url.pathname + page.url.search, { invalidateAll: true });
				},
				onError: (msg) => {
					toast(`TV detection job error: ${msg}`, 'bad');
					detecting = false;
					activeJobId = null;
				}
			},
			{
				eventsUrl: `/api/jobs/${jobId}/snapshot`
			}
		);
	}

	async function cancelCurrentJob() {
		if (!activeJobId) return;
		try {
			await cancelJob(fetch, activeJobId);
			toast('Cancellation requested', 'info');
			trackStop?.();
			detecting = false;
			activeJobId = null;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not cancel job', 'bad');
		}
	}

	onDestroy(() => {
		trackStop?.();
	});
</script>

<svelte:head>
	<title>TV Letterbox Workspace | Marquee</title>
</svelte:head>

<section class="page letterbox-tv-list">
	<SectionHeader title="Television Letterbox" subtitle="Manage crop tags for TV episodes." />

	<!-- Active job progress banner -->
	{#if detecting && activeJobId}
		<div class="progress-banner glass-panel animate-fade-in">
			<div class="progress-details">
				<span class="spinner"></span>
				<div>
					<h3>TV Letterbox Scan In Progress</h3>
					<p class="progress-desc">
						Status: <strong class="capitalize">{jobStatus || 'running'}</strong> · {progressDone} / {progressTotal}
						shows completed
					</p>
				</div>
			</div>
			<div class="progress-bar-container">
				<div class="progress-bar-fill" style={`width: ${progress}%`}></div>
			</div>
			<button class="btn btn-bad btn-sm" onclick={cancelCurrentJob}>Cancel</button>
		</div>
	{/if}

	<!-- Header control row -->
	<div class="controls-card glass-panel">
		<!-- Search & filters -->
		<div class="filters-row">
			<input
				type="search"
				placeholder="Search shows..."
				class="search-input"
				bind:value={searchQuery}
				onkeydown={(e) => e.key === 'Enter' && updateFilters()}
			/>

			<select class="filter-select" bind:value={filterVerdict} onchange={updateFilters}>
				<option value="">All Verdicts</option>
				<option value="needs_action">Needs Action</option>
				<option value="treated">Treated</option>
				<option value="unanalyzed">Unanalyzed</option>
				<option value="widescreen">Widescreen</option>
				<option value="open_matte">Open Matte</option>
				<option value="pillarbox">Pillarbox</option>
			</select>

			<select class="filter-select" bind:value={filterUniformity} onchange={updateFilters}>
				<option value="">All Uniformity</option>
				<option value="uniform">Uniform</option>
				<option value="clean_mixed">Clean Mix</option>
				<option value="dirty_mixed">Dirty Mix</option>
			</select>

			<select class="filter-select" bind:value={filterHasCandidates} onchange={updateFilters}>
				<option value="">All Candidates State</option>
				<option value="true">Has Candidates</option>
				<option value="false">No Candidates</option>
			</select>

			<button class="btn btn-outline" onclick={updateFilters}>Apply</button>
			{#if searchQuery || filterVerdict || filterUniformity || filterHasCandidates}
				<button class="btn btn-outline" onclick={clearFilters}>Clear</button>
			{/if}
		</div>

		<!-- Action buttons -->
		<div class="actions-row">
			<div class="toggle-container">
				<label class="toggle-switch">
					<input type="checkbox" bind:checked={exhaustive} />
					<span class="slider"></span>
				</label>
				<span class="toggle-label">Exhaustive Mode</span>
			</div>

			<button
				class="btn btn-primary"
				onclick={runBatch}
				disabled={detecting || (items.length === 0 && Object.keys(selectedIds).length === 0)}
			>
				{Object.values(selectedIds).filter(Boolean).length > 0
					? `Detect Selected (${Object.values(selectedIds).filter(Boolean).length})`
					: 'Detect Library'}
			</button>
		</div>
	</div>

	<!-- Shows list table -->
	<div class="table-container glass-panel">
		{#if items.length === 0}
			<div class="empty-state">No matching TV series found.</div>
		{:else}
			<table class="shows-table">
				<thead>
					<tr>
						<th class="checkbox-col">
							<input
								type="checkbox"
								checked={allSelected}
								indeterminate={someSelected}
								onclick={toggleSelectAll}
							/>
						</th>
						<th>Show Title</th>
						<th class="verdict-col">Verdict</th>
						<th class="bar-col">Episode Status</th>
						<th class="ar-col">Dominant AR</th>
						<th class="uniformity-col">Uniformity</th>
					</tr>
				</thead>
				<tbody>
					{#each items as item (item.series_id)}
						{@const state =
							item.rollup.verdict === 'ok' ? null : LETTERBOX_TV_VERDICT_META[item.rollup.verdict]}
						<tr class:selected={selectedIds[item.series_id]}>
							<td class="checkbox-col">
								<input
									type="checkbox"
									checked={Boolean(selectedIds[item.series_id])}
									onclick={() => toggleSelect(item.series_id)}
								/>
							</td>
							<td class="title-col">
								<a href={`/letterbox/tv/${item.series_id}`} class="show-link">
									<span class="show-title">{item.title}</span>
									{#if item.year}<span class="show-year">({item.year})</span>{/if}
									{#if item.active_job_ids && item.active_job_ids.length > 0}
										<span class="spinner-sm text-good" title="Scan running"></span>
									{/if}
								</a>
							</td>
							<td class="verdict-col">
								<div class="verdict-chips">
									{#if state}
										<span class="verdict-chip" style={`--c: var(--${state.tone})`}>
											{state.label}
										</span>
									{/if}
									{#each item.rollup.content_types as contentType (contentType.type)}
										{@const content = LETTERBOX_TV_CONTENT_META[contentType.type]}
										<span
											class="verdict-chip"
											style={`--c: var(--${content.tone})`}
											title={`${content.label}: ${contentType.count}`}
										>
											{content.label}
										</span>
									{/each}
								</div>
							</td>
							<td class="bar-col">
								<SegmentedBar
									segments={segmentsForRollup(item.rollup)}
									total={item.rollup.episodes_total}
									fractionText={getClearFraction(item.rollup)}
								/>
							</td>
							<td class="ar-col font-mono">
								{item.dominant_aspect_label || '—'}
							</td>
							<td class="uniformity-col">
								<UniformityChip uniformity={item.rollup.uniformity} />
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</section>

<style>
	.letterbox-tv-list {
		display: flex;
		flex-direction: column;
		gap: 24px;
		max-width: 1200px;
		margin: 0 auto;
		padding: 24px;
	}

	.glass-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 20px;
		box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
	}

	/* Progress banner */
	.progress-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 20px;
		border-left: 4px solid var(--good);
	}
	.progress-details {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-shrink: 0;
	}
	.progress-details h3 {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
	}
	.progress-desc {
		margin: 2px 0 0 0;
		font-size: 11px;
		color: var(--muted);
	}
	.progress-bar-container {
		flex: 1;
		height: 6px;
		background: var(--ink3);
		border-radius: 3px;
		overflow: hidden;
	}
	.progress-bar-fill {
		height: 100%;
		background: var(--good);
		border-radius: 3px;
		transition: width 0.3s ease;
	}

	/* Controls card */
	.controls-card {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.filters-row {
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}
	.search-input {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 6px 12px;
		font-size: 12px;
		color: var(--text);
		flex: 1;
		min-width: 180px;
	}
	.filter-select {
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 6px 10px;
		font-size: 12px;
		color: var(--text);
		cursor: pointer;
	}
	.actions-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		border-top: 1px solid var(--line);
		padding-top: 12px;
	}
	.toggle-container {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.toggle-label {
		font-size: 12px;
		font-weight: 600;
		color: var(--muted);
	}

	/* Toggle Switch */
	.toggle-switch {
		position: relative;
		display: inline-block;
		width: 28px;
		height: 16px;
		flex-shrink: 0;
	}
	.toggle-switch input {
		opacity: 0;
		width: 0;
		height: 0;
	}
	.slider {
		position: absolute;
		cursor: pointer;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		background-color: var(--ink3);
		transition: 0.2s;
		border-radius: 16px;
		border: 1px solid var(--line);
	}
	.slider:before {
		position: absolute;
		content: '';
		height: 10px;
		width: 10px;
		left: 2px;
		bottom: 2px;
		background-color: var(--text);
		transition: 0.2s;
		border-radius: 50%;
	}
	input:checked + .slider {
		background-color: var(--good);
		border-color: var(--good);
	}
	input:checked + .slider:before {
		transform: translateX(12px);
		background-color: #241a04;
	}

	/* Table styling */
	.table-container {
		padding: 0;
		overflow: hidden;
	}
	.empty-state {
		padding: 40px;
		text-align: center;
		color: var(--muted);
	}
	.shows-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-size: 13px;
	}
	.shows-table th {
		background: var(--ink2);
		padding: 12px 16px;
		font-weight: 700;
		color: var(--muted);
		border-bottom: 1px solid var(--line);
	}
	.shows-table td {
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
		vertical-align: middle;
	}
	.shows-table tr:hover {
		background: var(--ink2);
	}
	.shows-table tr.selected {
		background: color-mix(in srgb, var(--gold) 4%, var(--panel));
	}

	.checkbox-col {
		width: 40px;
		text-align: center;
		padding: 0 8px 0 16px !important;
	}
	.checkbox-col input {
		cursor: pointer;
	}

	.show-link {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		text-decoration: none;
		color: var(--text);
		font-weight: 700;
		transition: color 0.15s ease;
	}
	.show-link:hover {
		color: var(--gold-deep);
	}
	.show-year {
		color: var(--muted);
		font-weight: 400;
		font-size: 12px;
	}

	.verdict-chip {
		display: inline-flex;
		align-items: center;
		font-size: 11px;
		font-weight: 600;
		padding: 2px 8px;
		border-radius: 999px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 25%, transparent);
		white-space: nowrap;
	}
	.verdict-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}

	/* Spinner */
	.spinner {
		width: 16px;
		height: 16px;
		border: 2px solid var(--line);
		border-top: 2px solid var(--good);
		border-radius: 50%;
		animation: spin 1s linear infinite;
		display: inline-block;
	}
	.spinner-sm {
		width: 12px;
		height: 12px;
		border: 1.5px solid transparent;
		border-top: 1.5px solid var(--good);
		border-radius: 50%;
		animation: spin 1s linear infinite;
		display: inline-block;
	}
	@keyframes spin {
		0% {
			transform: rotate(0deg);
		}
		100% {
			transform: rotate(360deg);
		}
	}

	.text-good {
		color: var(--good);
	}

	/* Buttons */
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		font-size: 12px;
		font-weight: 600;
		cursor: pointer;
		transition: all 0.15s ease;
		border: 1px solid transparent;
		background: var(--ink3);
		color: var(--text);
		text-decoration: none;
	}
	.btn:hover {
		filter: brightness(1.1);
	}
	.btn-primary {
		background: var(--gold);
		color: #241a04;
		border-color: var(--gold-deep);
	}
	.btn-primary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.btn-outline {
		background: transparent;
		border-color: var(--line2);
	}
	.btn-outline:hover {
		background: var(--ink2);
	}
	.btn-bad {
		background: var(--bad);
		color: white;
		border-color: color-mix(in srgb, var(--bad) 80%, black);
	}
	.btn-sm {
		padding: 4px 8px;
		font-size: 11px;
	}
	.capitalize {
		text-transform: capitalize;
	}
</style>
