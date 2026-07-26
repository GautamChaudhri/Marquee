<script lang="ts">
	import { goto } from '$app/navigation';
	import type { MovieListItem } from '$lib/api/types';
	import PosterThumb from './PosterThumb.svelte';

	let { items }: { items: MovieListItem[] } = $props();
</script>

<div class="grid">
	{#each items as m (m.id)}
		<button class="cell" onclick={() => goto(`/films/${m.id}`)} title={m.title}>
			<PosterThumb
				title={m.title}
				year={m.year}
				posterStatus={m.poster_status}
				posterUrl={m.poster_url}
			/>
			<div class="cap">{m.title}</div>
		</button>
	{/each}
</div>

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(124px, 1fr));
		gap: 14px;
	}
	.cell {
		background: transparent;
		border: none;
		padding: 0;
		text-align: left;
		display: flex;
		flex-direction: column;
		gap: 6px;
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
		font-size: 12px;
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
