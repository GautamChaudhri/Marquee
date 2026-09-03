<script lang="ts">
	import { getSeasonPosterUrl, getSeriesPosterUrl, posterThumbUrl } from '$lib/api/poster-urls';
	import type { SeriesListItem } from '$lib/api/types';
	import PosterThumb from './PosterThumb.svelte';

	let { series }: { series: SeriesListItem } = $props();

	const seasons = $derived([...series.seasons].sort((a, b) => a.season_number - b.season_number));

	function seasonLabel(seasonNumber: number): string {
		return `S${String(seasonNumber).padStart(2, '0')}`;
	}
</script>

<!-- The strip is independently focusable so keyboard users can horizontally scroll long shows. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	class="poster-strip"
	role="region"
	aria-label={`${series.title} show and season posters`}
	tabindex="0"
>
	<div class="poster-tile" data-poster-kind="show">
		<PosterThumb
			title={series.title}
			year={series.year}
			imageAlt={`${series.title} show poster`}
			gradientKey={series.title}
			posterUrl={series.poster.has_poster
				? posterThumbUrl(getSeriesPosterUrl(series.id, series.poster.version))
				: null}
		/>
		<span>Show</span>
	</div>

	{#each seasons as season (season.id)}
		{@const label = seasonLabel(season.season_number)}
		<div class="poster-tile" data-poster-kind="season" data-season-number={season.season_number}>
			<PosterThumb
				title={label}
				imageAlt={`${series.title} ${label} poster`}
				gradientKey={series.title}
				fallbackPlacement="center"
				posterUrl={season.poster.has_poster
					? posterThumbUrl(getSeasonPosterUrl(season.id, season.poster.version))
					: null}
			/>
			<span>{label}</span>
		</div>
	{/each}
</div>

<style>
	.poster-strip {
		display: flex;
		gap: 10px;
		min-width: 0;
		overflow-x: auto;
		overscroll-behavior-x: contain;
		padding: 3px 3px 8px;
		scrollbar-width: thin;
		scrollbar-color: var(--line2) transparent;
	}
	.poster-strip:hover,
	.poster-strip:focus-visible {
		scrollbar-color: var(--faint) transparent;
	}
	.poster-strip:focus-visible {
		border-radius: 8px;
		outline: 2px solid var(--gold);
		outline-offset: 1px;
	}
	.poster-strip::-webkit-scrollbar {
		height: 8px;
	}
	.poster-strip::-webkit-scrollbar-track {
		background: transparent;
	}
	.poster-strip::-webkit-scrollbar-thumb {
		border: 2px solid transparent;
		border-radius: 99px;
		background-clip: content-box;
		background-color: var(--line2);
	}
	.poster-strip:hover::-webkit-scrollbar-thumb,
	.poster-strip:focus-visible::-webkit-scrollbar-thumb {
		background-color: var(--faint);
	}
	.poster-tile {
		display: flex;
		flex: 0 0 96px;
		width: 96px;
		flex-direction: column;
		gap: 6px;
	}
	.poster-tile :global(.poster) {
		box-sizing: border-box;
		width: 96px;
		height: 144px;
		border-radius: 10px;
	}
	.poster-tile > span {
		overflow: hidden;
		color: var(--muted);
		font-size: 11px;
		line-height: 1.2;
		text-align: center;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
