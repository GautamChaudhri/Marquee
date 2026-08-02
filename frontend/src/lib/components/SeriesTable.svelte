<script lang="ts">
	import type { SeriesListItem } from '$lib/api/types';
	import { deriveArtworkCoverage } from '$lib/tv-artwork-coverage';
	import ArtworkCoverage from './ArtworkCoverage.svelte';
	import SeriesPosterStrip from './SeriesPosterStrip.svelte';

	let { items }: { items: SeriesListItem[] } = $props();
</script>

<div class="series-table" role="table" aria-label="Television series and poster previews">
	<div class="table-head" role="rowgroup">
		<div role="row"><span role="columnheader">Series and posters</span></div>
	</div>
	<div class="table-body" role="rowgroup">
		{#each items as series (series.id)}
			<div class="series-row" role="row">
				<div class="series-cell" role="cell">
					<div class="series-heading">
						<ArtworkCoverage coverage={deriveArtworkCoverage(series)} mode="dot" />
						<a
							class="series-link"
							href={`/television/${series.id}`}
							aria-label={`Open ${series.title}${series.year ? `, ${series.year}` : ''}`}
						>
							<strong>{series.title}</strong>
							<small>{series.year ?? 'Year unknown'}</small>
						</a>
					</div>
					<SeriesPosterStrip {series} />
				</div>
			</div>
		{/each}
	</div>
</div>

<style>
	.table-head {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}
	.table-body {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}
	.series-row {
		min-width: 0;
		padding: 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		transition: background 0.1s ease;
	}
	.series-row:hover,
	.series-row:focus-within {
		background: var(--panel2);
	}
	.series-cell {
		min-width: 0;
	}
	.series-heading {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		min-width: 0;
	}
	.series-heading :global(.coverage) {
		margin-top: 5px;
	}
	.series-link {
		display: flex;
		min-width: 0;
		flex-direction: column;
		border-radius: 5px;
		color: var(--text);
	}
	.series-link strong {
		overflow: hidden;
		font-size: 14px;
		font-weight: 600;
		line-height: 1.25;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.series-link small {
		font-size: 11.5px;
		font-weight: 400;
		line-height: 1.25;
		color: var(--muted);
	}
	.series-link:hover strong {
		color: var(--gold);
	}
	.series-heading + :global(.poster-strip) {
		margin-top: 10px;
	}
	@media (prefers-reduced-motion: reduce) {
		.series-row {
			transition: none;
		}
	}
	@media (max-width: 820px) {
		.table-body {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
