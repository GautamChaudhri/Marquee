<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onDestroy } from 'svelte';
	import { api, call } from '$lib/api/client';
	import type { Job, Movie } from '$lib/api/types';
	import { describeJob, followJob, isFinal } from '$lib/features/jobs/jobs';
	import {
		DEFAULT_FILTERS,
		filtersFromParams,
		isFiltered,
		movieQuery,
		paramsFromFilters,
		type LibraryFilters
	} from '$lib/features/library/filters';
	import MovieTile from '$lib/features/library/MovieTile.svelte';
	import { toast } from '$lib/stores/toast';
	import Button from '$lib/ui/Button.svelte';
	import Notice from '$lib/ui/Notice.svelte';

	const filters = $derived(filtersFromParams(page.url.searchParams));

	let movies = $state<Movie[]>([]);
	let total = $state<number | null>(null);
	let pageLoaded = $state(0);
	let loading = $state(false);
	let failure = $state<string | null>(null);
	let search = $state(page.url.searchParams.get('q') ?? '');
	let generation = 0;

	const hasMore = $derived(total !== null && movies.length < total);

	async function load(next: number, current: LibraryFilters, token: number) {
		loading = true;
		failure = null;
		const result = await call(
			api.GET('/api/movies', { params: { query: movieQuery(current, next) } })
		);
		if (token !== generation) return; // the filters changed meanwhile
		loading = false;
		if (!result.ok) {
			failure = result.message;
			return;
		}
		movies = next === 1 ? result.data.items : [...movies, ...result.data.items];
		total = result.data.total;
		pageLoaded = next;
	}

	$effect(() => {
		const current = filters;
		generation += 1;
		movies = [];
		total = null;
		pageLoaded = 0;
		void load(1, current, generation);
	});

	function loadMore() {
		if (!loading && hasMore) void load(pageLoaded + 1, filters, generation);
	}

	function apply(changes: Partial<LibraryFilters>) {
		const params = paramsFromFilters({ ...filters, ...changes });
		const query = params.size ? `?${params}` : '/';
		void goto(query, { replaceState: true, keepFocus: true, noScroll: true });
	}

	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(value: string) {
		search = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => apply({ q: value }), 250);
	}

	function clearFilters() {
		search = '';
		apply({ ...DEFAULT_FILTERS, sort: filters.sort });
	}

	// Loading more as the owner scrolls: a marker under the grid that starts the next page
	// when it comes into view.
	function moreWhenVisible(node: HTMLElement) {
		const observer = new IntersectionObserver(
			(entries) => {
				if (entries.some((entry) => entry.isIntersecting)) loadMore();
			},
			{ rootMargin: '600px 0px' }
		);
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	}

	// "Sync now"
	let syncJob = $state<Job | null>(null);
	let syncNote = $state<string | null>(null);
	let syncBusy = $state(false);
	let stopFollowing: () => void = () => {};

	async function syncNow() {
		syncBusy = true;
		syncNote = null;
		const result = await call(api.POST('/api/sync'));
		syncBusy = false;
		if (!result.ok) {
			syncNote = result.message;
			toast(result.message, 'bad');
			return;
		}
		syncJob = result.data;
		syncNote =
			result.status === 202
				? `Sync queued as job ${result.data.id}.`
				: `A sync is already ${result.data.state} (job ${result.data.id}).`;
		stopFollowing();
		stopFollowing = followJob(result.data.id, (job) => {
			syncJob = job;
			if (isFinal(job.state)) {
				toast(
					`Sync job ${job.id}: ${describeJob(job)}`,
					job.state === 'succeeded' ? 'good' : 'bad'
				);
				if (job.state === 'succeeded') apply({});
			}
		});
	}

	onDestroy(() => {
		stopFollowing();
		clearTimeout(searchTimer);
	});
</script>

<svelte:head>
	<title>Library · Marquee</title>
</svelte:head>

<h1 class="sr-only">Library</h1>

<div class="toolbar">
	<input
		class="control search"
		type="search"
		placeholder="Search titles"
		aria-label="Search titles"
		value={search}
		oninput={(e) => onSearch(e.currentTarget.value)}
	/>
	<div class="filters">
		<label class="field">
			Poster
			<select
				class="control"
				value={filters.poster}
				onchange={(e) => apply({ poster: e.currentTarget.value as LibraryFilters['poster'] })}
			>
				<option value="any">Any</option>
				<option value="yes">Has a poster</option>
				<option value="no">No poster</option>
			</select>
		</label>
		<label class="field">
			Whose
			<select
				class="control"
				value={filters.origin}
				onchange={(e) => apply({ origin: e.currentTarget.value as LibraryFilters['origin'] })}
			>
				<option value="any">Anyone’s</option>
				<option value="library">Yours</option>
				<option value="marquee">Marquee’s</option>
			</select>
		</label>
		<label class="field">
			Runs
			<select
				class="control"
				value={filters.run}
				onchange={(e) => apply({ run: e.currentTarget.value as LibraryFilters['run'] })}
			>
				<option value="any">Any</option>
				<option value="yes">Has a run</option>
				<option value="no">Never run</option>
			</select>
		</label>
		<label class="field">
			Sort
			<select
				class="control"
				value={filters.sort}
				onchange={(e) => apply({ sort: e.currentTarget.value as LibraryFilters['sort'] })}
			>
				<option value="title">Title</option>
				<option value="year">Year</option>
				<option value="added">Added</option>
			</select>
		</label>
	</div>
	<div class="sync">
		<Button variant="primary" busy={syncBusy} onclick={syncNow}>Sync now</Button>
	</div>
</div>

{#if syncNote}
	<p class="sync-note" role="status" data-testid="sync-note">
		{syncNote}
		{#if syncJob}
			<span class="job-state">{describeJob(syncJob)}</span>
			<a href="/activity?job={syncJob.id}">Follow it in Activity</a>
		{/if}
	</p>
{/if}

<p class="count" aria-live="polite">
	{#if total !== null}
		{total}
		{total === 1 ? 'movie' : 'movies'}{isFiltered(filters) ? ' match' : ''}
	{/if}
</p>

{#if failure && movies.length === 0}
	<Notice tone="bad" message="The library could not be loaded. {failure}">
		{#snippet action()}
			<Button onclick={() => load(1, filters, generation)}>Try again</Button>
		{/snippet}
	</Notice>
{:else if total === 0}
	<Notice
		message={isFiltered(filters)
			? 'No movie matches.'
			: 'The library is empty. Sync it from Radarr.'}
	>
		{#snippet action()}
			{#if isFiltered(filters)}<Button onclick={clearFilters}>Clear filters</Button>{/if}
		{/snippet}
	</Notice>
{:else}
	<div class="grid" data-testid="library-grid">
		{#each movies as movie (movie.id)}
			<MovieTile {movie} />
		{/each}
		{#if loading}
			{#each Array.from({ length: movies.length ? 6 : 12 }, (_, i) => i) as i (i)}
				<div class="ghost" aria-hidden="true"></div>
			{/each}
		{/if}
	</div>
	{#if loading && movies.length === 0}
		<p class="sr-only" role="status">Loading the library…</p>
	{/if}
	{#if failure && movies.length > 0}
		<Notice tone="bad" message="More movies could not be loaded. {failure}">
			{#snippet action()}<Button onclick={loadMore}>Try again</Button>{/snippet}
		</Notice>
	{:else if hasMore}
		<div class="more" use:moreWhenVisible>
			<Button onclick={loadMore} busy={loading}>
				Load more ({total! - movies.length} left)
			</Button>
		</div>
	{/if}
{/if}

<style>
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 10px 14px;
		margin-bottom: 10px;
	}
	.search {
		flex: 1 1 220px;
		max-width: 360px;
	}
	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.sync {
		margin-left: auto;
	}
	.sync-note {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 10px;
		margin: 0 0 10px;
		font-size: 13px;
	}
	.job-state {
		color: var(--muted);
	}
	.sync-note a {
		color: var(--gold-copy);
	}
	.count {
		margin: 0 0 12px;
		min-height: 1.2em;
		color: var(--muted);
		font-size: 12.5px;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
		gap: 18px 14px;
	}
	.ghost {
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		background: var(--panel2);
		animation: mq-pulse 1.4s ease-in-out infinite;
	}
	.more {
		display: flex;
		justify-content: center;
		margin-top: 22px;
	}
	@media (max-width: 720px) {
		.search {
			max-width: none;
			flex-basis: 100%;
		}
		.filters {
			display: grid;
			grid-template-columns: repeat(2, minmax(0, 1fr));
			width: 100%;
		}
		.sync {
			margin-left: 0;
		}
		.grid {
			grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
			gap: 14px 10px;
		}
	}
</style>
