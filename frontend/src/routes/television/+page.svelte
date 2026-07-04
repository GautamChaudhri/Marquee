<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import { compareBySortTitle, sortTitle } from '$lib/sort-title';
	import { posterStatusFromSummary, posterStatusMeta, toneVar, type Tone } from '$lib/display';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import type { SeriesListItem } from '$lib/api/types';
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

	function seasonBadge(item: SeriesListItem): {
		label: string;
		tone: Tone;
	} {
		if (item.season_poster_status === 'complete') return { label: 'Seasons ✓', tone: 'good' };
		if (item.season_poster_status === 'missing') return { label: 'Seasons ✗', tone: 'bad' };
		return {
			label: `Seasons ${item.seasons_with_poster}/${item.downloaded_seasons}`,
			tone: 'gold'
		};
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

	function seasonBadgeLabel(item: SeriesListItem): string {
		return seasonBadge(item).label;
	}

	function seasonBadgeTone(item: SeriesListItem): Tone {
		return seasonBadge(item).tone;
	}
</script>

<SectionHeader
	title="Television"
	subtitle={data.data ? `${data.data.total} series` : 'Series library'}
/>

<div class="filters">
	<label class="search">
		<Icon name="search" size={15} />
		<input
			placeholder="Search titles…"
			value={q}
			oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
		/>
	</label>
	<select value={data.query.sort ?? 'title'} onchange={(e) => apply({ sort: selectVal(e) })}>
		<option value="title">Sort: Title</option>
		<option value="year">Sort: Year</option>
	</select>
</div>

{#if data.error}
	<div class="state error">
		<strong>Couldn't reach the backend.</strong>
		<span>{data.error}</span>
		<button onclick={() => apply({})}>Retry</button>
	</div>
{:else if items.length === 0}
	<div class="state empty">No series match this search.</div>
{:else}
	<div class="grid">
		{#each items as series (series.id)}
			<button class="cell" onclick={() => goto(`/television/${series.id}`)} title={series.title}>
				<PosterThumb
					title={series.title}
					year={series.year}
					posterStatus={posterStatusFromSummary(series.poster)}
					posterUrl={series.poster.has_poster ? `/api/library/series/${series.id}/poster` : null}
				/>
				<div class="cap">
					<div class="title">{series.title}</div>
					<div class="meta">
						<span>{series.year ?? '—'}</span>
						<span
							class="poster"
							style={`--c:${toneVar(posterStatusMeta[posterStatusFromSummary(series.poster)].tone)}`}
						>
							<StatusDot
								tone={posterStatusMeta[posterStatusFromSummary(series.poster)].tone}
								size={6}
							/>
							{posterStatusMeta[posterStatusFromSummary(series.poster)].label}
						</span>
					</div>
					<div class="season-chip" style={`--c:${toneVar(seasonBadgeTone(series))}`}>
						<StatusDot tone={seasonBadgeTone(series)} size={6} />
						{seasonBadgeLabel(series)}
					</div>
				</div>
			</button>
		{/each}
	</div>
{/if}

<style>
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
		background: transparent;
		border: none;
		outline: none;
		color: var(--text);
		font-size: 13.5px;
		padding: 9px 0;
	}
	select {
		padding: 9px 12px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		font-size: 13px;
	}
	.state {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 40px 24px;
		text-align: center;
		background: var(--panel);
		color: var(--muted);
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
	}
	.state.error strong {
		color: var(--bad);
	}
	.state button {
		padding: 7px 16px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
		gap: 16px;
	}
	.cell {
		background: transparent;
		border: none;
		padding: 0;
		text-align: left;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.cell :global(.poster) {
		transition:
			transform 0.12s ease,
			border-color 0.12s ease;
	}
	.cell:hover :global(.poster) {
		transform: translateY(-2px);
		border-color: var(--faint);
	}
	.cap {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.title {
		font-size: 12px;
		color: var(--text);
		font-weight: 600;
		line-height: 1.25;
	}
	.meta {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		font-size: 11px;
		color: var(--muted);
	}
	.poster,
	.season-chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--c);
	}
	.season-chip {
		font-size: 11px;
	}
</style>
