<script lang="ts">
	import type { Movie } from '#lib/api/types.ts';
	import { thumbnailUrl } from './poster';

	let { movie }: { movie: Movie } = $props();
	let broken = $state(false);
</script>

<a class="tile" href="/movies/{movie.id}" data-testid="movie-tile">
	<div class="frame">
		{#if movie.poster && !broken}
			<img
				src={thumbnailUrl(movie.poster, 320)}
				alt=""
				loading="lazy"
				decoding="async"
				width="320"
				height="480"
				onerror={() => (broken = true)}
			/>
		{:else}
			<div class="none">
				<span>{movie.poster ? 'Poster unavailable' : 'No poster'}</span>
			</div>
		{/if}
		{#if movie.poster_origin === 'marquee'}
			<span class="mark" title="A poster Marquee wrote">Marquee</span>
		{/if}
	</div>
	<div class="caption">
		<span class="title">{movie.title}</span>
		<span class="year">{movie.year ?? '—'}</span>
	</div>
</a>

<style>
	.tile {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}
	.frame {
		position: relative;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel2);
		border: 1px solid var(--line);
	}
	.tile:hover .frame {
		border-color: var(--faint);
	}
	img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.none {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		padding: 8px;
		text-align: center;
		color: var(--faint);
		font-size: 12px;
		border: 1px dashed var(--line2);
		border-radius: var(--radius-sm);
	}
	.mark {
		position: absolute;
		top: 6px;
		left: 6px;
		padding: 2px 7px;
		border-radius: var(--radius-pill);
		background: var(--gold);
		color: var(--on-gold);
		font-size: 10.5px;
		font-weight: 700;
		letter-spacing: 0.02em;
	}
	.caption {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 6px;
		min-width: 0;
	}
	.title {
		font-size: 12.5px;
		font-weight: 550;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.year {
		flex: none;
		font-size: 11.5px;
		color: var(--muted);
		font-variant-numeric: tabular-nums;
	}
</style>
