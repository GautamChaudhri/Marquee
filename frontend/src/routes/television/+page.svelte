<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import ArtworkCoverage from '$lib/components/ArtworkCoverage.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import SeriesTable from '$lib/components/SeriesTable.svelte';
	import { compareBySortTitle, sortTitle } from '$lib/sort-title';
	import { televisionMode } from '$lib/theme';
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

	const items = $derived.by(() => {
		const list = [...(data.data?.items ?? [])];
		const query = q.trim().toLowerCase();
		const filtered = query
			? list.filter((item) => sortTitle(item.title).toLowerCase().includes(query))
			: list;
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

{#if data.error}
	<div class="state error">
		<strong>Couldn't reach the backend.</strong>
		<span>{data.error}</span>
		<button onclick={() => apply({})}>Retry</button>
	</div>
{:else if items.length === 0}
	<div class="state empty">No series match this search.</div>
{:else if $televisionMode === 'grid'}
	<div class="grid">
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
						year={series.year}
						imageAlt={`${series.title} show poster`}
						posterUrl={series.poster.has_poster ? `/api/library/series/${series.id}/poster` : null}
					/>
					<ArtworkCoverage {coverage} mode="pin" decorative />
				</div>
				<div class="cap">
					<div class="title">{series.title}</div>
					<div class="year">{series.year ?? 'Year unknown'}</div>
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
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(124px, 1fr));
		gap: 14px;
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
		min-width: 0;
		flex-direction: column;
	}
	.title {
		display: -webkit-box;
		overflow: hidden;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		font-size: 12px;
		font-weight: 600;
		line-height: 1.2;
		color: var(--text);
	}
	.year {
		font-size: 10.5px;
		line-height: 1.25;
		color: var(--muted);
	}
</style>
