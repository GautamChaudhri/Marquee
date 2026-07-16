<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import SegmentedBar from '$lib/components/SegmentedBar.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import CoverageChips from '$lib/components/subtitles/CoverageChips.svelte';
	import type { AudioSubsTvItem } from '$lib/api/types';

	let { data } = $props();

	let items = $derived(data.result?.items || []);
	let total = $derived(data.result?.total || 0);

	let q = $state(page.url.searchParams.get('q') || '');
	let status = $state(page.url.searchParams.get('status') || '');
	let uniformity = $state(page.url.searchParams.get('uniformity') || '');
	let missingLanguage = $state(page.url.searchParams.get('missing_language') || '');
	let sortBy = $state(page.url.searchParams.get('sort_by') || 'title');

	$effect(() => {
		q = page.url.searchParams.get('q') || '';
		status = page.url.searchParams.get('status') || '';
		uniformity = page.url.searchParams.get('uniformity') || '';
		missingLanguage = page.url.searchParams.get('missing_language') || '';
		sortBy = page.url.searchParams.get('sort_by') || 'title';
	});

	function updateFilters() {
		const url = new URL(page.url);
		if (q) url.searchParams.set('q', q);
		else url.searchParams.delete('q');
		if (status) url.searchParams.set('status', status);
		else url.searchParams.delete('status');
		if (uniformity) url.searchParams.set('uniformity', uniformity);
		else url.searchParams.delete('uniformity');
		if (missingLanguage) url.searchParams.set('missing_language', missingLanguage);
		else url.searchParams.delete('missing_language');
		if (sortBy !== 'title') url.searchParams.set('sort_by', sortBy);
		else url.searchParams.delete('sort_by');

		goto(url.pathname + url.search, { keepFocus: true, replaceState: true });
	}

	function handleRowClick(id: number) {
		goto(`/audio-subs/tv/${id}`);
	}
</script>

<svelte:head>
	<title>TV Subtitles & Audio — Marquee</title>
</svelte:head>

<div class="page-container">
	<div class="top-nav">
		<a href="/audio-subs" class="back-link">← Back to Dashboard</a>
	</div>

	<SectionHeader
		title="Television Subtitle & Audio Library"
		subtitle="Overview of localization, audio tracks availability and missing languages for series."
	/>

	<!-- Filter Controls -->
	<div class="controls-panel mq-rise">
		<div class="search-box">
			<span class="icon">🔍</span>
			<input
				type="text"
				placeholder="Search by show title..."
				bind:value={q}
				oninput={updateFilters}
			/>
		</div>

		<div class="filters-grid">
			<label class="filter-field">
				<span>Status</span>
				<select bind:value={status} onchange={updateFilters}>
					<option value="">All Statuses</option>
					<option value="ok">OK (Fully Covered)</option>
					<option value="gaps">Gaps (Missing Languages)</option>
					<option value="none_met">None Met (No Preferred)</option>
					<option value="unknown">Unknown</option>
				</select>
			</label>

			<label class="filter-field">
				<span>Uniformity</span>
				<select bind:value={uniformity} onchange={updateFilters}>
					<option value="">All</option>
					<option value="uniform">Uniform</option>
					<option value="uniform_by_season">Uniform by Season</option>
					<option value="mixed">Mixed</option>
				</select>
			</label>

			<label class="filter-field">
				<span>Missing Language</span>
				<input
					type="text"
					placeholder="e.g. en, fr"
					bind:value={missingLanguage}
					oninput={updateFilters}
				/>
			</label>

			<label class="filter-field">
				<span>Sort By</span>
				<select bind:value={sortBy} onchange={updateFilters}>
					<option value="title">Title</option>
					<option value="status">Status</option>
					<option value="coverage">Dub Coverage</option>
				</select>
			</label>
		</div>
	</div>

	{#if data.error}
		<div class="error-banner">
			<p>Failed to load TV inventory: {data.error}</p>
		</div>
	{/if}

	<!-- Show List Table -->
	<div class="list-wrapper mq-rise">
		{#if items.length === 0}
			<div class="empty-state">No TV shows found matching current filters.</div>
		{:else}
			<table class="tv-table">
				<thead>
					<tr>
						<th>Title</th>
						<th>Status</th>
						<th>Episode Coverage Bar</th>
						<th>Dubbing Coverage</th>
						<th>Missing Languages</th>
						<th>Uniformity</th>
					</tr>
				</thead>
				<tbody>
					{#each items as item (item.series_id)}
						{@const r = item.rollup}
						{@const segments = [
							{ key: 'OK', count: r.status_counts?.ok || 0, tone: 'good' as const },
							{ key: 'Audio Gap', count: r.status_counts?.audio_gap || 0, tone: 'warn' as const },
							{
								key: 'Subtitle Gap',
								count: r.status_counts?.subtitle_gap || 0,
								tone: 'info' as const
							},
							{ key: 'Both Gap', count: r.status_counts?.both_gap || 0, tone: 'bad' as const },
							{ key: 'Unknown', count: r.status_counts?.unknown || 0, tone: 'muted' as const }
						]}
						<tr onclick={() => handleRowClick(item.series_id)} class="interactive-row">
							<td class="title-cell">
								<strong class="title">{item.title}</strong>
								<span class="year">{item.year}</span>
							</td>
							<td>
								<CoverageChips status={r.status} />
							</td>
							<td class="bar-cell">
								<SegmentedBar
									{segments}
									total={r.episodes_total}
									fractionText={item.episode_fraction}
								/>
							</td>
							<td>
								<div class="dub-cov">
									<strong
										>{Math.round(
											(item.dub_coverage.ok / (item.dub_coverage.of || 1)) * 100
										)}%</strong
									>
									<span class="fraction">({item.dub_coverage.ok}/{item.dub_coverage.of})</span>
								</div>
							</td>
							<td>
								<div class="missing-chips">
									{#each r.missing_audio_languages || [] as lang}
										<span class="missing-chip audio">− {lang} audio</span>
									{/each}
									{#each r.missing_subtitle_languages || [] as lang}
										<span class="missing-chip sub">− {lang} sub</span>
									{/each}
									{#if !r.missing_audio_languages?.length && !r.missing_subtitle_languages?.length}
										<span class="all-covered">None</span>
									{/if}
								</div>
							</td>
							<td>
								<UniformityChip uniformity={item.uniformity} />
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</div>

<style>
	.page-container {
		display: flex;
		flex-direction: column;
		gap: 20px;
		padding-bottom: 32px;
	}
	.top-nav {
		margin-bottom: 4px;
	}
	.back-link {
		color: var(--muted);
		font-size: 13px;
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		transition: color 0.15s;
	}
	.back-link:hover {
		color: var(--gold);
	}
	.controls-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.search-box {
		position: relative;
		width: 100%;
	}
	.search-box .icon {
		position: absolute;
		left: 12px;
		top: 50%;
		transform: translateY(-50%);
		font-size: 14px;
		color: var(--muted);
		pointer-events: none;
	}
	.search-box input {
		width: 100%;
		padding: 10px 12px 10px 38px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13.5px;
		outline: none;
	}
	.search-box input:focus {
		border-color: var(--gold);
	}
	.filters-grid {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 16px;
	}
	@media (max-width: 800px) {
		.filters-grid {
			grid-template-columns: repeat(2, 1fr);
		}
	}
	@media (max-width: 480px) {
		.filters-grid {
			grid-template-columns: 1fr;
		}
	}
	.filter-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.filter-field span {
		font-size: 11px;
		text-transform: uppercase;
		font-weight: 700;
		color: var(--faint2);
	}
	.filter-field select,
	.filter-field input {
		padding: 8px 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		color: var(--text);
		font-size: 13px;
		outline: none;
	}
	.filter-field select:focus,
	.filter-field input:focus {
		border-color: var(--gold);
	}
	.list-wrapper {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
	}
	.empty-state {
		text-align: center;
		padding: 48px;
		color: var(--muted);
	}
	.tv-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
	}
	th {
		padding: 12px 16px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		border-bottom: 1px solid var(--line);
	}
	td {
		padding: 14px 16px;
		font-size: 13px;
		border-bottom: 1px solid var(--line2);
		color: var(--text);
		vertical-align: middle;
	}
	tr:last-child td {
		border-bottom: none;
	}
	.interactive-row {
		cursor: pointer;
		transition: background-color 0.12s;
	}
	.interactive-row:hover {
		background: var(--ink3);
	}
	.title-cell {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.title-cell .title {
		font-size: 13.5px;
		font-weight: 650;
	}
	.title-cell .year {
		font-size: 11.5px;
		color: var(--muted);
	}
	.bar-cell {
		width: 200px;
	}
	.dub-cov {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.dub-cov .fraction {
		font-size: 11px;
		color: var(--muted);
	}
	.missing-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.missing-chip {
		font-size: 11px;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 4px;
		white-space: nowrap;
	}
	.missing-chip.audio {
		background: color-mix(in srgb, var(--warn) 10%, transparent);
		color: var(--warn);
		border: 1px solid color-mix(in srgb, var(--warn) 20%, transparent);
	}
	.missing-chip.sub {
		background: color-mix(in srgb, var(--info) 10%, transparent);
		color: var(--info);
		border: 1px solid color-mix(in srgb, var(--info) 20%, transparent);
	}
	.all-covered {
		color: var(--muted);
		font-style: italic;
	}
	.error-banner {
		padding: 14px;
		background: rgba(239, 83, 80, 0.1);
		border: 1px solid var(--bad);
		color: var(--bad);
		border-radius: var(--radius-sm);
		font-size: 13.5px;
	}
</style>
