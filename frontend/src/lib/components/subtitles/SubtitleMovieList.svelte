<script lang="ts">
	import type { MovieListItem } from '$lib/api/types';
	import SubtitleMovieRow from './SubtitleMovieRow.svelte';

	let {
		movies
	}: {
		movies: MovieListItem[];
	} = $props();

	let searchQuery = $state('');
	let filterStatus = $state<'all' | 'ok' | 'gap' | 'none'>('all');

	let filteredMovies = $derived(
		movies.filter((m) => {
			const matchesSearch = m.title.toLowerCase().includes(searchQuery.toLowerCase());
			if (filterStatus === 'all') return matchesSearch;
			if (filterStatus === 'ok') return matchesSearch && m.subtitle_status === 'ok';
			if (filterStatus === 'gap') return matchesSearch && m.subtitle_status === 'gap';
			if (filterStatus === 'none') return matchesSearch && !m.subtitle_status;
			return matchesSearch;
		})
	);
</script>

<div class="controls">
	<div class="search-box">
		<span class="icon">🔍</span>
		<input
			type="text"
			placeholder="Search movies..."
			bind:value={searchQuery}
		/>
	</div>
	<div class="filter-group">
		<button class="filter-btn" class:active={filterStatus === 'all'} onclick={() => filterStatus = 'all'}>All</button>
		<button class="filter-btn" class:active={filterStatus === 'ok'} onclick={() => filterStatus = 'ok'}>OK</button>
		<button class="filter-btn" class:active={filterStatus === 'gap'} onclick={() => filterStatus = 'gap'}>Gaps</button>
		<button class="filter-btn" class:active={filterStatus === 'none'} onclick={() => filterStatus = 'none'}>Unscanned</button>
	</div>
</div>

<div class="list">
	<div class="row head">
		<span></span>
		<span>Title</span>
		<span>Audio</span>
		<span>Audio Channels</span>
		<span>Subtitles</span>
		<span>Embedded</span>
		<span>External</span>
		<span>Status</span>
		<span></span>
	</div>

	{#if filteredMovies.length === 0}
		<div class="empty">No movies found matching current filters.</div>
	{:else}
		{#each filteredMovies as m (m.id)}
			<SubtitleMovieRow
				movie={m}
			/>
		{/each}
	{/if}
</div>

<style>
	.controls {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
		gap: 16px;
		flex-wrap: wrap;
	}
	.search-box {
		position: relative;
		flex: 1;
		min-width: 240px;
		max-width: 400px;
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
		font-size: 14px;
		outline: none;
		transition: border-color 0.2s, box-shadow 0.2s;
	}
	.search-box input:focus {
		border-color: var(--gold);
		box-shadow: 0 0 0 2px var(--gold-soft);
	}
	.filter-group {
		display: flex;
		gap: 4px;
		background: var(--panel2);
		padding: 3px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
	}
	.filter-btn {
		background: transparent;
		border: none;
		padding: 6px 12px;
		color: var(--muted);
		font-size: 12.5px;
		font-weight: 500;
		border-radius: calc(var(--radius-sm) - 2px);
		cursor: pointer;
		transition: color 0.15s, background-color 0.15s;
	}
	.filter-btn:hover {
		color: var(--text);
	}
	.filter-btn.active {
		background: var(--panel);
		color: var(--gold);
	}
	.list {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--panel);
	}
	.row.head {
		display: grid;
		grid-template-columns: 40px minmax(0, 1fr) 120px 120px 120px 100px 100px 100px 40px;
		align-items: center;
		gap: 12px;
		width: 100%;
		padding: 12px 16px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		cursor: default;
		border-bottom: 1px solid var(--line);
	}
	.empty {
		padding: 32px;
		text-align: center;
		color: var(--muted);
		font-size: 14px;
	}
</style>
