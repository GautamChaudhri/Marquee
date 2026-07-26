<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { filmMode } from '$lib/theme';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import FilmList from '$lib/components/FilmList.svelte';
	import FilmGrid from '$lib/components/FilmGrid.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Seed the search box from the URL once; the input is user-controlled after.
	// svelte-ignore state_referenced_locally
	let q = $state(data.query.q ?? '');
	let debounce: ReturnType<typeof setTimeout>;

	const totalPages = $derived(
		data.data ? Math.max(1, Math.ceil(data.data.total / data.data.page_size)) : 1
	);
	const curPage = $derived(data.query.page ?? 1);

	function apply(params: Record<string, string | undefined>, resetPage = true) {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		for (const [k, v] of Object.entries(params)) {
			if (v) sp.set(k, v);
			else sp.delete(k);
		}
		if (resetPage) sp.delete('page');
		goto(`/films?${sp.toString()}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function onSearch(value: string) {
		q = value;
		clearTimeout(debounce);
		debounce = setTimeout(() => apply({ q: value || undefined }), 250);
	}

	function selectVal(e: Event): string | undefined {
		return (e.currentTarget as HTMLSelectElement).value || undefined;
	}
</script>

<SectionHeader title="Films" subtitle={data.data ? `${data.data.total} movies` : 'Movie library'}>
	{#snippet action()}
		<div class="modes">
			<button class:on={$filmMode === 'list'} onclick={() => ($filmMode = 'list')} title="List">
				<Icon name="list" size={16} />
			</button>
			<button class:on={$filmMode === 'grid'} onclick={() => ($filmMode = 'grid')} title="Grid">
				<Icon name="grid" size={16} />
			</button>
		</div>
	{/snippet}
</SectionHeader>

<div class="filters">
	<label class="search">
		<Icon name="search" size={15} />
		<input
			placeholder="Search titles…"
			value={q}
			oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
		/>
	</label>

	<select
		value={data.query.poster_status ?? ''}
		onchange={(e) => apply({ poster_status: selectVal(e) })}
	>
		<option value="">Poster: any</option>
		<option value="deployed">Deployed</option>
		<option value="approved">Approved</option>
		<option value="review">Review</option>
		<option value="missing">No poster</option>
	</select>

	<select value={data.query.sort ?? 'title'} onchange={(e) => apply({ sort: selectVal(e) }, false)}>
		<option value="title">Sort: Title</option>
		<option value="year">Sort: Year</option>
		<option value="added">Sort: Added</option>
	</select>
</div>

<div class="quick-filters">
	<button
		class="qf-pill"
		class:on={data.query.poster_status === 'review'}
		onclick={() =>
			apply({
				poster_status: data.query.poster_status === 'review' ? undefined : 'review'
			})}
	>
		Needs review
		{#if data.query.poster_status === 'review' && data.data}
			<span class="qf-count">{data.data.total}</span>
		{/if}
	</button>
</div>

{#if data.error}
	<div class="state error">
		<strong>Couldn't reach the backend.</strong>
		<span>{data.error}</span>
		<button onclick={() => apply({})}>Retry</button>
	</div>
{:else if !data.data || data.data.items.length === 0}
	<div class="state empty">No movies match these filters.</div>
{:else}
	{#if $filmMode === 'grid'}
		<FilmGrid items={data.data.items} />
	{:else}
		<FilmList items={data.data.items} />
	{/if}

	{#if totalPages > 1}
		<div class="pager">
			<button disabled={curPage <= 1} onclick={() => apply({ page: String(curPage - 1) }, false)}>
				Prev
			</button>
			<span>Page {curPage} / {totalPages}</span>
			<button
				disabled={curPage >= totalPages}
				onclick={() => apply({ page: String(curPage + 1) }, false)}
			>
				Next
			</button>
		</div>
	{/if}
{/if}

<style>
	.modes {
		display: flex;
		gap: 2px;
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: 8px;
		padding: 2px;
	}
	.modes button {
		display: grid;
		place-items: center;
		width: 30px;
		height: 26px;
		border: none;
		border-radius: 6px;
		background: transparent;
		color: var(--muted);
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
	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 14px;
		margin-top: 18px;
		font-size: 13px;
		color: var(--muted);
	}
	.pager button {
		padding: 7px 16px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
	}
	.pager button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
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
		border-radius: 99px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--muted);
		font-size: 12.5px;
		font-weight: 500;
		transition:
			background 0.1s,
			color 0.1s;
	}
	.qf-pill:hover {
		color: var(--text);
		background: var(--panel2);
	}
	.qf-pill.on {
		background: var(--gold);
		border-color: var(--gold-deep);
		color: var(--on-gold);
	}
	.qf-count {
		font-family: var(--font-mono);
		font-size: 11px;
		padding: 0 5px;
		border-radius: 99px;
		background: color-mix(in srgb, currentColor 18%, transparent);
	}
</style>
