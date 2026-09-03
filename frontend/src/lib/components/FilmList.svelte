<script lang="ts">
	import { posterThumbUrl } from '$lib/api/poster-urls';
	import type { MovieListItem } from '$lib/api/types';
	import { deriveFilmArtworkStatus } from '$lib/library-artwork';
	import GenreSummary from './GenreSummary.svelte';
	import PosterThumb from './PosterThumb.svelte';
	import StatusDot from './StatusDot.svelte';

	let { items }: { items: MovieListItem[] } = $props();
</script>

<div class="film-table" role="table" aria-label="Films with poster previews and genres">
	<div class="table-head" role="rowgroup">
		<div role="row"><span role="columnheader">Films, posters, and genres</span></div>
	</div>
	<div class="table-body" role="rowgroup">
		{#each items as movie (movie.id)}
			{@const artwork = deriveFilmArtworkStatus(movie)}
			<div class="film-row" role="row">
				<div class="film-cell" role="cell">
					<div class="film-heading">
						<span
							class="status-marker"
							role="img"
							aria-label={artwork.accessibleLabel}
							title={artwork.accessibleLabel}
						>
							<StatusDot tone={artwork.tone} title={artwork.accessibleLabel} />
						</span>
						<a
							class="film-link"
							href={`/films/${movie.id}`}
							aria-label={`Open ${movie.title}${movie.year ? `, ${movie.year}` : ''}`}
						>
							<strong>{movie.title}</strong>
							<small>{movie.year || 'Year unknown'}</small>
						</a>
					</div>

					<div class="film-preview">
						<div class="poster-preview">
							<PosterThumb
								title={movie.title}
								imageAlt={`${movie.title} poster`}
								posterUrl={posterThumbUrl(movie.poster_url)}
								fallbackPlacement="bottom-left"
							/>
						</div>
						<GenreSummary genres={movie.genres} />
					</div>
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
	.film-row {
		min-width: 0;
		padding: 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		transition: background 0.1s ease;
	}
	.film-row:hover,
	.film-row:focus-within {
		background: var(--panel2);
	}
	.film-cell {
		min-width: 0;
	}
	.film-heading {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		min-width: 0;
	}
	.status-marker {
		display: inline-flex;
		margin-top: 5px;
		flex: none;
	}
	.film-link {
		display: flex;
		min-width: 0;
		flex-direction: column;
		border-radius: 5px;
		color: var(--text);
	}
	.film-link strong {
		overflow: hidden;
		font-size: 14px;
		font-weight: 600;
		line-height: 1.25;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.film-link small {
		font-size: 11.5px;
		font-weight: 400;
		line-height: 1.25;
		color: var(--muted);
	}
	.film-link:hover strong {
		color: var(--gold);
	}
	.film-preview {
		display: grid;
		grid-template-columns: 96px minmax(0, 1fr);
		align-items: start;
		gap: 16px;
		min-width: 0;
		margin-top: 10px;
		padding: 3px;
	}
	.poster-preview {
		width: 96px;
	}
	.poster-preview :global(.poster) {
		box-sizing: border-box;
		width: 96px;
		height: 144px;
		border-radius: 10px;
	}
	@media (prefers-reduced-motion: reduce) {
		.film-row {
			transition: none;
		}
	}
	@media (max-width: 820px) {
		.table-body {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
