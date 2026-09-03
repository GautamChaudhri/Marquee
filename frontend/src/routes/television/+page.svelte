<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { getSeriesPosterUrl, posterThumbUrl } from '$lib/api/poster-urls';
	import Icon from '$lib/components/Icon.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import SeriesTable from '$lib/components/SeriesTable.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import { compareBySortTitle, sortTitle } from '$lib/sort-title';
	import { libraryPosterSize, televisionMode } from '$lib/theme';
	import { deriveArtworkCoverage } from '$lib/tv-artwork-coverage';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// svelte-ignore state_referenced_locally
	let q = $state(data.query.q ?? '');
	let debounce: ReturnType<typeof setTimeout>;

	function apply(params: Record<string, string | undefined>) {
		const url = new URL(page.url);
		const sp = url.searchParams;
		for (const [k, v] of Object.entries(params)) {
			if (v) sp.set(k, v);
			else sp.delete(k);
		}
		goto(`/television?${sp.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function onSearch(value: string) {
		q = value;
		clearTimeout(debounce);
		debounce = setTimeout(() => apply({ q: value || undefined }), 250);
	}

	function selectVal(e: Event): string | undefined {
		return (e.currentTarget as HTMLSelectElement).value || undefined;
	}

	function toggleReview() {
		clearTimeout(debounce);
		if (data.query.review) {
			apply({ review: undefined });
			return;
		}
		q = '';
		apply({ q: undefined, review: '1' });
	}

	const items = $derived.by(() => {
		const list = [...(data.data?.items ?? [])];
		const query = q.trim().toLowerCase();
		const searched = query
			? list.filter((item) => sortTitle(item.title).toLowerCase().includes(query))
			: list;
		const filtered = data.query.review ? searched.filter((item) => item.review_pending) : searched;
		const sort = data.query.sort ?? 'title';
		filtered.sort((a, b) =>
			sort === 'year'
				? (b.year ?? 0) - (a.year ?? 0) || compareBySortTitle(a.title, b.title)
				: compareBySortTitle(a.title, b.title)
		);
		return filtered;
	});
</script>

<div class="filters">
	<label class="search">
		<Icon name="search" size={15} />
		<input
			aria-label="Search television titles"
			placeholder="Search titles…"
			value={q}
			oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
		/>
	</label>
	<select
		aria-label="Sort television"
		value={data.query.sort ?? 'title'}
		onchange={(e) => apply({ sort: selectVal(e) })}
	>
		<option value="title">Sort: Title</option>
		<option value="year">Sort: Year</option>
	</select>
	{#if $televisionMode === 'grid'}
		<select aria-label="Poster size" bind:value={$libraryPosterSize}>
			<option value="small">Size: Small</option>
			<option value="medium">Size: Medium</option>
			<option value="large">Size: Large</option>
		</select>
	{/if}
	<div class="modes" role="group" aria-label="Television library view">
		<button
			type="button"
			class:on={$televisionMode === 'list'}
			onclick={() => ($televisionMode = 'list')}
			aria-label="Table view"
			aria-pressed={$televisionMode === 'list'}
			title="Table view"
		>
			<Icon name="list" size={16} />
		</button>
		<button
			type="button"
			class:on={$televisionMode === 'grid'}
			onclick={() => ($televisionMode = 'grid')}
			aria-label="Grid view"
			aria-pressed={$televisionMode === 'grid'}
			title="Grid view"
		>
			<Icon name="grid" size={16} />
		</button>
	</div>
</div>

{#if data.data && data.data.review_pending_total > 0}
	<div class="quick-filters">
		<button
			class="qf-pill"
			class:on={data.query.review}
			aria-pressed={data.query.review}
			onclick={toggleReview}
		>
			Needs review
			<span class="qf-count">{data.data.review_pending_total}</span>
		</button>
	</div>
{/if}

{#if data.error}
	<div class="state error">
		<strong>Couldn't reach the backend.</strong>
		<span>{data.error}</span>
		<button onclick={() => apply({})}>Retry</button>
	</div>
{:else if items.length === 0}
	<div class="state empty">No series match this search.</div>
{:else if $televisionMode === 'grid'}
	<div class="grid size-{$libraryPosterSize}">
		{#each items as series (series.id)}
			{@const coverage = deriveArtworkCoverage(series)}
			<button
				class="cell"
				onclick={() => goto(`/television/${series.id}`)}
				title={series.title}
				aria-label={`Open ${series.title}${series.year ? `, ${series.year}` : ''}. ${coverage.accessibleLabel}`}
			>
				<div class="poster-shell">
					<PosterThumb
						title={series.title}
						imageAlt={`${series.title} show poster`}
						posterUrl={series.poster.has_poster
							? posterThumbUrl(getSeriesPosterUrl(series.id, series.poster.version))
							: null}
					/>
				</div>
				<div class="cap" aria-hidden="true">
					<StatusDot tone={coverage.tone} title={coverage.label} />
					<span class="year">{series.year ?? 'Year unknown'}</span>
				</div>
			</button>
		{/each}
	</div>
{:else}
	<SeriesTable {items} />
{/if}

<style>
	.modes {
		display: flex;
		gap: 2px;
		padding: 2px;
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel);
	}
	.modes button {
		display: grid;
		width: 30px;
		height: 26px;
		place-items: center;
		border: none;
		border-radius: 6px;
		background: transparent;
		color: var(--muted);
	}
	.modes button:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.modes button.on {
		background: var(--gold);
		color: var(--on-gold);
	}
	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		margin-bottom: 16px;
	}
	.search {
		display: flex;
		align-items: center;
		gap: 8px;
		flex: 1;
		min-width: 200px;
		padding: 0 12px;
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel);
		color: var(--muted);
	}
	.search input {
		flex: 1;
		padding: 9px 0;
		border: none;
		outline: none;
		background: transparent;
		color: var(--text);
		font-size: 13.5px;
	}
	select {
		padding: 9px 12px;
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel);
		color: var(--text);
		font-size: 13px;
	}
	.state {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
		padding: 40px 24px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--muted);
		text-align: center;
	}
	.state.error strong {
		color: var(--bad);
	}
	.state button {
		padding: 7px 16px;
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel2);
		color: var(--text);
	}
	.grid {
		--poster-card-min: 136px;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(var(--poster-card-min), 1fr));
		gap: 14px;
	}
	.grid.size-small {
		--poster-card-min: 104px;
	}
	.grid.size-large {
		--poster-card-min: 184px;
	}
	.cell {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 0;
		border: none;
		background: transparent;
		text-align: left;
	}
	.poster-shell {
		position: relative;
	}
	.cell .poster-shell :global(.poster) {
		transition:
			transform 0.12s ease,
			border-color 0.12s ease;
	}
	.cell:hover .poster-shell :global(.poster) {
		transform: translateY(-2px);
		border-color: var(--faint);
	}
	.cap {
		display: flex;
		align-items: center;
		gap: 7px;
		min-width: 0;
	}
	.year {
		font-size: 10.5px;
		line-height: 1.25;
		color: var(--muted);
	}
	.quick-filters {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
		margin-bottom: 14px;
	}
	.qf-pill {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 5px 12px;
		border: 1px solid var(--line2);
		border-radius: 99px;
		background: var(--panel);
		color: var(--muted);
		font-size: 12.5px;
		font-weight: 500;
		transition:
			background 0.1s,
			color 0.1s;
	}
	.qf-pill:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.qf-pill.on {
		border-color: color-mix(in srgb, var(--review) 45%, var(--line2));
		background: color-mix(in srgb, var(--review) 14%, var(--panel));
		color: var(--review);
	}
	.qf-count {
		padding: 0 5px;
		border-radius: 99px;
		background: color-mix(in srgb, currentColor 18%, transparent);
		font-family: var(--font-mono);
		font-size: 11px;
	}
	@media (prefers-reduced-motion: reduce) {
		.qf-pill {
			transition: none;
		}
	}
	@media (max-width: 300px) {
		.grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
