<script lang="ts">
	import { posterStatusMeta } from '$lib/display';
	import type { MovieListItem, PosterStatus } from '$lib/api/types';
	import PosterThumb from './PosterThumb.svelte';
	import StatusDot from './StatusDot.svelte';

	let { items }: { items: MovieListItem[] } = $props();

	const artworkLabels = {
		missing: 'Missing',
		review: 'Review',
		approved: 'Approved',
		deployed: 'Deployed'
	} satisfies Record<PosterStatus, string>;
</script>

<!-- Keyboard focus exposes horizontal scrolling without a pointer. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div class="table-scroll" role="region" aria-label="Films table" tabindex="0">
	<table>
		<caption>Films and their artwork status and genres</caption>
		<colgroup>
			<col class="film-col" />
			<col class="artwork-col" />
			<col class="genres-col" />
		</colgroup>
		<thead>
			<tr>
				<th scope="col">Film</th>
				<th scope="col">Artwork</th>
				<th scope="col">Genres</th>
			</tr>
		</thead>
		<tbody>
			{#each items as m (m.id)}
				<tr>
					<td>
						<a class="film-link" href={`/films/${m.id}`} aria-label={`Open ${m.title}, ${m.year}`}>
							<span class="thumb" aria-hidden="true">
								<PosterThumb
									title={m.title}
									imageAlt=""
									posterUrl={m.poster_url}
									fallbackPlacement="hidden"
								/>
							</span>
							<span class="title">
								<strong>{m.title}</strong>
								<small>{m.year}</small>
							</span>
						</a>
					</td>
					<td>
						<span class="artwork">
							<StatusDot
								tone={posterStatusMeta[m.poster_status].tone}
								title={artworkLabels[m.poster_status]}
							/>
							<span>{artworkLabels[m.poster_status]}</span>
						</span>
					</td>
					<td><span class="genres">{m.genres?.length ? m.genres.join(', ') : '—'}</span></td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.table-scroll {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		overflow-x: auto;
		background: var(--panel);
	}
	table {
		width: 100%;
		min-width: 560px;
		border-collapse: collapse;
		table-layout: fixed;
	}
	caption {
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
	.film-col {
		width: 48%;
	}
	.artwork-col {
		width: 20%;
	}
	.genres-col {
		width: 32%;
	}
	th,
	td {
		padding: 9px 14px;
		text-align: left;
		vertical-align: middle;
		border-bottom: 1px solid var(--line);
	}
	th {
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--muted);
		background: var(--ink2);
	}
	tbody tr:last-child td {
		border-bottom: none;
	}
	tbody tr {
		transition: background 0.1s ease;
	}
	tbody tr:hover,
	tbody tr:focus-within {
		background: var(--panel2);
	}
	.film-link {
		display: grid;
		grid-template-columns: 32px minmax(0, 1fr);
		align-items: center;
		gap: 11px;
		min-width: 0;
		color: var(--text);
	}
	.thumb {
		width: 32px;
		flex: none;
	}
	.title {
		display: flex;
		flex-direction: column;
		min-width: 0;
	}
	.title strong {
		font-size: 13.5px;
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.film-link:hover .title strong {
		color: var(--gold);
	}
	.title small {
		font-size: 11.5px;
		color: var(--muted);
	}
	.artwork {
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.artwork {
		white-space: nowrap;
	}
	.genres {
		display: -webkit-box;
		overflow: hidden;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		font-size: 12.5px;
		line-height: 1.35;
		color: var(--muted);
	}
	@media (prefers-reduced-motion: reduce) {
		tbody tr {
			transition: none;
		}
	}
</style>
