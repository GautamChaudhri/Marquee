<script lang="ts">
	import { goto } from '$app/navigation';
	import { posterThumbUrl } from '$lib/api/poster-urls';
	import type { MovieListItem } from '$lib/api/types';
	import { deriveFilmArtworkStatus } from '$lib/library-artwork';
	import { libraryPosterSize } from '$lib/theme';
	import PosterThumb from './PosterThumb.svelte';
	import StatusDot from './StatusDot.svelte';

	let { items }: { items: MovieListItem[] } = $props();
</script>

<div class="grid size-{$libraryPosterSize}">
	{#each items as m (m.id)}
		{@const artwork = deriveFilmArtworkStatus(m)}
		<button
			class="cell"
			onclick={() => goto(`/films/${m.id}`)}
			title={m.title}
			aria-label={`Open ${m.title}, ${m.year}. ${artwork.accessibleLabel}`}
		>
			<PosterThumb
				title={m.title}
				imageAlt={`${m.title} poster`}
				posterUrl={posterThumbUrl(m.poster_url)}
			/>
			<div class="cap" aria-hidden="true">
				<StatusDot tone={artwork.tone} title={artwork.label} />
				<span>{m.year}</span>
			</div>
		</button>
	{/each}
</div>

<style>
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
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 10.5px;
		line-height: 1.25;
		color: var(--muted);
	}
	@media (max-width: 300px) {
		.grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
