<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { posterThumbUrl } from '$lib/api/poster-urls';
	import TasteMap from '$lib/components/TasteMap.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { getExemplarNeighbors, getTasteMap, rebuildTasteMap } from '$lib/api/taste';
	import { kindLabel, pointTitle, subjectHref } from '$lib/taste/map-points';
	import type { ColorMode, ViewMode } from '$lib/taste/map-points';
	import { toast } from '$lib/toast';
	import type { TasteMapData, TasteMapPoint, TasteNeighbor } from '$lib/api/types';
	import type { MapView } from './+page';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const VIEWS: { id: MapView; label: string; library: 'movies' | 'tv' }[] = [
		{ id: 'films', label: 'Films', library: 'movies' },
		{ id: 'shows', label: 'Shows', library: 'tv' },
		{ id: 'seasons', label: 'Show Seasons', library: 'tv' }
	];

	// Seeded from the URL once; the switch below is what drives it afterwards, and it
	// rewrites the URL itself — so re-reading `data` here would only fight with it.
	let view = $state<MapView>(untrack(() => data.view));
	let mode = $state<ViewMode>('2d');
	let colorBy = $state<ColorMode>('cluster');
	let showNoise = $state(true);
	let selected = $state<TasteMapPoint | null>(null);
	let neighbors = $state<TasteNeighbor[]>([]);
	let neighborsLoading = $state(false);
	let rebuilding = $state(false);

	const library = $derived(VIEWS.find((v) => v.id === view)?.library ?? 'movies');

	// One projection per library; the film/show/season switch is a read of it. Shows
	// and seasons share a coordinate space on purpose — that is what makes a season's
	// artwork comparable to the show art it sits under.
	let maps = $state<Record<'movies' | 'tv', TasteMapData | null>>({ movies: null, tv: null });
	let loading = $state(false);
	let error = $state<string | null>(null);
	let requested: string | null = null;

	$effect(() => {
		const target = library;
		if (requested === target) return;
		requested = target;
		void loadMap(target);
	});

	async function loadMap(target: 'movies' | 'tv') {
		loading = true;
		error = null;
		try {
			maps = { ...maps, [target]: await getTasteMap(fetch, target) };
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load the taste map';
		} finally {
			loading = false;
		}
	}

	const mapData = $derived(maps[library]);
	const points = $derived.by(() => {
		const all = mapData?.points ?? [];
		if (view === 'films') return all.filter((p) => p.asset_kind === 'movie');
		return all.filter((p) => p.asset_kind === (view === 'shows' ? 'show' : 'season'));
	});
	const genresMissing = $derived(
		colorBy === 'genre' && points.length > 0 && points.every((p) => !p.genres?.length)
	);

	function pickView(next: MapView) {
		if (next === view) return;
		view = next;
		selected = null;
		neighbors = [];
		// The library rides in the URL so the section nav above carries it across.
		const nextLibrary = VIEWS.find((v) => v.id === next)?.library ?? 'movies';
		const query = nextLibrary === 'tv' ? `library=tv&view=${next}` : `view=${next}`;
		void goto(`/taste/map?${query}`, { replaceState: true, noScroll: true, keepFocus: true });
	}

	function select(point: TasteMapPoint | null) {
		selected = point;
		neighbors = [];
		if (!point) return;
		neighborsLoading = true;
		getExemplarNeighbors(fetch, point.name, library)
			.then((r) => {
				neighbors = r.neighbors;
			})
			.catch(() => toast('Could not load nearest exemplars', 'bad'))
			.finally(() => {
				neighborsLoading = false;
			});
	}

	async function rebuild() {
		if (rebuilding) return;
		rebuilding = true;
		try {
			const job = await rebuildTasteMap(fetch, library);
			toast(job.idempotent ? 'Taste map rebuild already running' : 'Taste map rebuild queued');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Map rebuild failed to start', 'bad');
		} finally {
			rebuilding = false;
		}
	}

	function formatName(name: string): string {
		return name.replace(/\.(jpg|jpeg|png|webp)$/i, '').replace(/[-_]/g, ' ');
	}

	const clusterName = $derived.by(() => {
		if (!selected || selected.cluster == null || selected.cluster === -1) return null;
		return mapData?.clusters?.find((c) => c.id === selected!.cluster)?.name ?? null;
	});
</script>

<h1 class="sr-only">Taste Map</h1>

<div class="toolbar">
	<nav class="views" aria-label="Map subject">
		{#each VIEWS as option (option.id)}
			<button
				class:on={view === option.id}
				aria-current={view === option.id ? 'true' : undefined}
				onclick={() => pickView(option.id)}
			>
				{option.label}
			</button>
		{/each}
	</nav>

	<div class="controls">
		<div class="segmented" role="group" aria-label="Projection">
			<button class:on={mode === '2d'} onclick={() => (mode = '2d')}>2D</button>
			<button class:on={mode === '3d'} onclick={() => (mode = '3d')}>3D</button>
		</div>
		<label class="select">
			<span class="sr-only">Colour points by</span>
			<select bind:value={colorBy}>
				<option value="cluster">Cluster</option>
				<option value="genre">Genre</option>
				<option value="aesthetic">Aesthetic</option>
				<option value="colorfulness">Colourfulness</option>
				<option value="year">Year</option>
				<option value="self_knn">Self k-NN</option>
			</select>
		</label>
		<button class="pill ghost" onclick={() => (showNoise = !showNoise)} aria-pressed={showNoise}>
			{showNoise ? 'Hide unclustered' : 'Show unclustered'}
		</button>
		<button class="pill quiet" onclick={rebuild} disabled={rebuilding}>
			{rebuilding ? 'Queuing…' : 'Rebuild map'}
		</button>
	</div>
</div>

{#if genresMissing}
	<p class="warn">
		No genre metadata for these points. Run <b>Enrich metadata</b> on the Engine page.
	</p>
{/if}

<div class="workspace">
	<div class="stage">
		{#if loading && !mapData}
			<div class="placeholder"><span class="spinner"></span>Loading the taste map…</div>
		{:else if error}
			<div class="placeholder bad">
				<Icon name="alert" size={22} />
				{error}
			</div>
		{:else if points.length === 0}
			<div class="placeholder">
				<Icon name="grid" size={26} stroke={1} />
				No points in this view yet. Rebuild the map after the profile covers them.
			</div>
		{:else}
			<TasteMap
				{points}
				clusters={mapData?.clusters ?? null}
				{mode}
				{colorBy}
				{showNoise}
				{selected}
				dataset={`${view}:${mapData?.projection.computed_at ?? ''}`}
				onSelect={select}
			/>
		{/if}
		{#if mapData && points.length > 0}
			<p class="stage-meta">
				{points.length} points · {mapData.projection.method.toUpperCase()} · {mapData.clusters
					?.length ?? 0} clusters
			</p>
		{/if}
	</div>

	<aside class="detail" aria-live="polite">
		{#if selected}
			<div class="detail-head">
				<span class="kind">{kindLabel(selected.asset_kind)}</span>
				<button class="close" onclick={() => select(null)} aria-label="Clear selection">
					<Icon name="x" size={14} />
				</button>
			</div>

			<div class="detail-poster">
				<PosterThumb
					title={selected.asset_kind === 'season' && selected.season_number != null
						? `S${String(selected.season_number).padStart(2, '0')}`
						: pointTitle(selected)}
					imageAlt={`${pointTitle(selected)} poster`}
					gradientKey={selected.name}
					fallbackPlacement={selected.asset_kind === 'season' ? 'center' : 'bottom-left'}
					posterUrl={posterThumbUrl(selected.poster_url)}
				/>
			</div>

			<h2>{pointTitle(selected)}</h2>
			{#if selected.year}<p class="year">{selected.year}</p>{/if}
			{#if selected.genres?.length}
				<ul class="genres">
					{#each selected.genres as genre (genre)}<li>{genre}</li>{/each}
				</ul>
			{/if}

			<dl class="facts">
				{#if clusterName}
					<div>
						<dt>Cluster</dt>
						<dd>{clusterName}</dd>
					</div>
				{:else if selected.is_noise}
					<div>
						<dt>Cluster</dt>
						<dd>Unclustered</dd>
					</div>
				{/if}
				{#if selected.aesthetic != null}
					<div>
						<dt>Aesthetic</dt>
						<dd class="mono">{selected.aesthetic.toFixed(1)}</dd>
					</div>
				{/if}
				{#if selected.colorfulness != null}
					<div>
						<dt>Colourfulness</dt>
						<dd class="mono">{selected.colorfulness.toFixed(0)}</dd>
					</div>
				{/if}
				<div>
					<dt>Self k-NN</dt>
					<dd class="mono">{selected.self_knn.toFixed(3)}</dd>
				</div>
			</dl>

			{#if subjectHref(selected)}
				<a class="pill quiet open" href={subjectHref(selected)}>
					Open in library <Icon name="chevron" size={13} />
				</a>
			{/if}

			<div class="neighbors">
				<h3>Nearest Artwork</h3>
				{#if neighborsLoading}
					<p class="hint">Loading…</p>
				{:else if neighbors.length === 0}
					<p class="hint">None found.</p>
				{:else}
					{#each neighbors as n (n.name)}
						<div class="neighbor">
							<span>{formatName(n.name)}</span>
							<span class="mono sim">{n.similarity.toFixed(3)}</span>
						</div>
					{/each}
				{/if}
			</div>
		{:else}
			<div class="detail-empty">
				<Icon name="taste" size={26} stroke={1} />
				<p>Select a point to see its artwork.</p>
			</div>
		{/if}
	</aside>
</div>

<style>
	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		flex-wrap: wrap;
		padding: 8px 10px;
		margin-bottom: 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius-pill);
		background: var(--panel);
	}
	.views,
	.segmented {
		display: inline-flex;
		gap: 2px;
		padding: 3px;
		border: 1px solid var(--line);
		border-radius: var(--radius-pill);
		background: var(--panel2);
	}
	.views button,
	.segmented button {
		padding: 6px 14px;
		border: none;
		border-radius: var(--radius-pill);
		background: transparent;
		color: var(--muted);
		font-size: 13px;
		font-weight: 500;
	}
	.views button:hover,
	.segmented button:hover {
		color: var(--text);
	}
	.views button.on,
	.segmented button.on {
		background: var(--gold);
		color: var(--on-gold);
	}
	.controls {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.select select {
		padding: 7px 12px;
		border-radius: var(--radius-pill);
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 13px;
		font-family: var(--font-sans);
	}
	.select select:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}

	.warn {
		margin: 0 0 12px;
		padding: 8px 14px;
		font-size: 12px;
		color: var(--warn-copy);
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 22%, transparent);
		border-radius: var(--radius-sm);
	}

	/* The map is the page: it takes the viewport it can, and the reading of whatever
	   is selected runs beside it rather than pushing it off screen. */
	.workspace {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 300px;
		/* minmax(0, 1fr): the row is distributed space, never content-sized. An
		   auto row would grow to fit whatever height Plotly last drew, which then
		   resizes Plotly taller — the loop that stretched this page downward. */
		grid-template-rows: minmax(0, 1fr);
		gap: 14px;
		height: calc(100vh - 240px);
		height: calc(100dvh - 240px);
		min-height: 460px;
	}
	.stage {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
	}
	.stage-meta {
		margin: 8px 2px 0;
		font-size: 11.5px;
		color: var(--faint);
	}
	.placeholder {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 12px;
		padding: 40px 24px;
		text-align: center;
		font-size: 13px;
		color: var(--faint);
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.placeholder.bad {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 35%, transparent);
	}
	.spinner {
		width: 22px;
		height: 22px;
		border: 2px solid var(--line2);
		border-top-color: var(--gold);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.spinner {
			animation: none;
		}
	}

	.detail {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 14px;
		min-height: 0;
		overflow-y: auto;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.detail-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}
	.kind {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		font-weight: 700;
		color: var(--gold-copy);
	}
	.close {
		display: inline-flex;
		padding: 4px;
		border: none;
		border-radius: var(--radius-sm);
		background: transparent;
		color: var(--faint);
	}
	.close:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.detail-poster {
		width: 100%;
		flex: 0 0 auto;
	}
	.detail-poster :global(.poster) {
		box-sizing: border-box;
		width: 100%;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
	}
	.detail h2 {
		margin: 2px 0 0;
		font-size: 15px;
		font-weight: 650;
		line-height: 1.3;
	}
	.year {
		margin: 0;
		font-size: 12px;
		color: var(--muted);
	}
	.genres {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		margin: 2px 0 0;
		padding: 0;
		list-style: none;
	}
	.genres li {
		padding: 3px 8px;
		border-radius: var(--radius-pill);
		border: 1px solid var(--line);
		background: var(--panel2);
		font-size: 11px;
		color: var(--muted);
	}
	.facts {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin: 6px 0 0;
		padding-top: 10px;
		border-top: 1px solid var(--line);
	}
	.facts div {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 10px;
	}
	.facts dt {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
	}
	.facts dd {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
		text-align: right;
	}
	.open {
		align-self: flex-start;
	}
	.neighbors {
		margin-top: 4px;
		padding-top: 10px;
		border-top: 1px solid var(--line);
	}
	.neighbors h3 {
		margin: 0 0 6px;
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.neighbor {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 8px;
		padding: 4px 0;
		font-size: 12px;
		color: var(--muted);
	}
	.neighbor span:first-child {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.sim {
		flex: none;
		color: var(--gold-copy);
		font-size: 11px;
	}
	.hint {
		margin: 0;
		font-size: 12px;
		color: var(--faint);
	}
	.detail-empty {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		text-align: center;
		color: var(--faint);
	}
	.detail-empty p {
		margin: 0;
		font-size: 12.5px;
	}
	.mono {
		font-family: var(--font-mono);
	}

	@media (max-width: 900px) {
		.workspace {
			grid-template-columns: minmax(0, 1fr);
			grid-template-rows: none;
			height: auto;
			min-height: 0;
		}
		/* The stacked layout is content-sized, so the plot needs a definite height
		   of its own here — flex: 1 against an auto container is self-referential. */
		.stage :global(.plot) {
			flex: none;
			height: 60vh;
			min-height: 320px;
		}
	}
</style>
