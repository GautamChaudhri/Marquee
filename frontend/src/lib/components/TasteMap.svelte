<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from '$lib/toast';
	import type { TasteMapData, TasteMapPoint, TasteNeighbor } from '$lib/api/types';
	import { getExemplarNeighbors } from '$lib/api/taste';
	import { countOfSubjects } from '$lib/taste/library-copy';

	let {
		mapData,
		library = 'movies',
		loading = false,
		error = null as string | null
	}: {
		mapData: TasteMapData | null;
		library?: 'movies' | 'tv';
		loading?: boolean;
		error?: string | null;
	} = $props();

	type ViewMode = '2d' | '3d';
	type ColorMode = 'cluster' | 'genre' | 'aesthetic' | 'colorfulness' | 'year' | 'self_knn';

	let mode = $state<ViewMode>('2d');
	let colorBy = $state<ColorMode>('cluster');
	let showNoise = $state(true);
	let selectedPoint = $state<TasteMapPoint | null>(null);
	let neighbors = $state<TasteNeighbor[]>([]);
	let neighborsLoading = $state(false);
	let plotEl = $state<HTMLDivElement | null>(null);
	let Plotly: typeof import('plotly.js-dist-min') | null = null;
	let resizeObs: ResizeObserver | null = null;

	// ── Color palettes (Marquee-dark compatible) ──────────────────────────
	const CLUSTER_COLORS = [
		'#ffc24b',
		'#5ca8fb',
		'#46d18a',
		'#f87171',
		'#b794f6',
		'#fb923c',
		'#4ade80',
		'#f472b6',
		'#60a5fa',
		'#fbbf24',
		'#a78bfa',
		'#34d399',
		'#fb7185',
		'#38bdf8',
		'#facc15',
		'#818cf8',
		'#2dd4bf',
		'#e879f9',
		'#94a3b8',
		'#f59e0b'
	];

	const GENRE_COLORS: Record<string, string> = {
		Action: '#f87171',
		Adventure: '#fb923c',
		Animation: '#fbbf24',
		Comedy: '#facc15',
		Crime: '#a3a3a3',
		Documentary: '#94a3b8',
		Drama: '#5ca8fb',
		Family: '#4ade80',
		Fantasy: '#b794f6',
		History: '#d4a574',
		Horror: '#8b5cf6',
		Music: '#f472b6',
		Mystery: '#6366f1',
		Romance: '#fb7185',
		'Science Fiction': '#38bdf8',
		Thriller: '#ef4444',
		War: '#78716c',
		Western: '#d4a574',
		'TV Movie': '#a78bfa'
	};

	function genreColor(genres: string[] | null): string {
		if (!genres || genres.length === 0) return '#5b6170';
		for (const g of genres) {
			if (GENRE_COLORS[g]) return GENRE_COLORS[g];
		}
		return '#5b6170';
	}

	function formatName(name: string): string {
		return name.replace(/\.(jpg|jpeg|png|webp)$/i, '').replace(/[-_]/g, ' ');
	}

	function displayTitle(point: TasteMapPoint): string {
		return point.movie_title || formatName(point.name);
	}

	function formatGenres(genres: string[] | null): string {
		return genres?.join(', ') ?? 'unknown';
	}

	// ── Build color arrays and legend ─────────────────────────────────────
	function buildColors(points: TasteMapPoint[]): {
		colors: string[];
		legendGroups: { name: string; color: string }[];
		colorbar?: { title: string; colorscale: [number, string][] };
		showLegend: boolean;
	} {
		if (colorBy === 'cluster') {
			const clusterColorsById: Record<number, string> = {};
			const clusterNamesById: Record<number, string> = {};
			if (mapData?.clusters) {
				for (const c of mapData.clusters) {
					clusterColorsById[c.id] = CLUSTER_COLORS[c.id % CLUSTER_COLORS.length];
					clusterNamesById[c.id] = c.name;
				}
			}
			const noiseColor = '#3f4452';
			const noiseCount = points.filter((p) => p.cluster == null || p.cluster === -1).length;
			const colors = points.map((p) =>
				p.cluster != null && p.cluster !== -1
					? (clusterColorsById[p.cluster] ?? noiseColor)
					: noiseColor
			);
			const legend = Object.entries(clusterColorsById)
				.map(([id, color]) => ({ id: Number(id), color }))
				.filter(({ id }) => Boolean(clusterNamesById[id]))
				.map(({ id, color }) => ({ name: clusterNamesById[id], color }));
			if (noiseCount > 0) {
				legend.push({ name: `Noise (${noiseCount})`, color: noiseColor });
			}
			return { colors, legendGroups: legend, showLegend: true };
		}

		if (colorBy === 'genre') {
			const colors = points.map((p) => genreColor(p.genres));
			const present = colors
				.map((_, i) => points[i].genres?.[0] ?? 'unknown')
				.filter((genre, index, all) => all.indexOf(genre) === index);
			const legend = present
				.filter((g) => GENRE_COLORS[g])
				.map((g) => ({ name: g, color: GENRE_COLORS[g] }));
			return { colors, legendGroups: legend, showLegend: true };
		}

		if (colorBy === 'aesthetic') {
			const vals = points.map((p) => p.aesthetic ?? 0);
			return {
				colors: vals.map((v) => aestheticColor(v)),
				legendGroups: [],
				colorbar: { title: 'Aesthetic', colorscale: aestheticScale() },
				showLegend: false
			};
		}

		if (colorBy === 'colorfulness') {
			const vals = points.map((p) => p.colorfulness ?? 0);
			return {
				colors: vals.map((v) => colorfulnessColor(v)),
				legendGroups: [],
				colorbar: { title: 'Colorfulness', colorscale: colorfulnessScale() },
				showLegend: false
			};
		}

		if (colorBy === 'year') {
			const years = points.map((p) => p.year ?? 2000);
			return {
				colors: years.map((y) => yearColor(y)),
				legendGroups: [],
				colorbar: { title: 'Year', colorscale: yearScale() },
				showLegend: false
			};
		}

		if (colorBy === 'self_knn') {
			const vals = points.map((p) => p.self_knn);
			return {
				colors: vals.map((v) => knnColor(v)),
				legendGroups: [],
				colorbar: { title: 'Self k-NN', colorscale: knnScale() },
				showLegend: false
			};
		}

		return { colors: points.map(() => '#ffc24b'), legendGroups: [], showLegend: false };
	}

	function aestheticColor(v: number): string {
		const t = Math.max(0, Math.min(1, (v - 2) / 7));
		const r = Math.round(56 + t * 199);
		const g = Math.round(160 + t * 34);
		const b = Math.round(251 - t * 176);
		return `rgb(${r},${g},${b})`;
	}

	function aestheticScale(): [number, string][] {
		const steps: [number, string][] = [];
		for (let i = 0; i <= 10; i++) {
			const t = i / 10;
			const v = 2 + t * 7;
			steps.push([v, aestheticColor(v)]);
		}
		return steps;
	}

	function colorfulnessColor(v: number): string {
		const t = Math.max(0, Math.min(1, v / 150));
		const r = Math.round(90 + t * 165);
		const g = Math.round(140 + t * 54);
		const b = Math.round(240 - t * 165);
		return `rgb(${r},${g},${b})`;
	}

	function colorfulnessScale(): [number, string][] {
		return [
			[0, 'rgb(90,140,240)'],
			[75, 'rgb(180,170,140)'],
			[150, 'rgb(255,194,75)']
		];
	}

	function yearColor(y: number): string {
		const t = Math.max(0, Math.min(1, (y - 1920) / 110));
		const r = Math.round(100 + t * 155);
		const g = Math.round(150 + t * 44);
		const b = Math.round(240 - t * 140);
		return `rgb(${r},${g},${b})`;
	}

	function yearScale(): [number, string][] {
		return [
			[1920, 'rgb(100,150,240)'],
			[1975, 'rgb(160,170,170)'],
			[2030, 'rgb(255,194,100)']
		];
	}

	function knnColor(v: number): string {
		const t = Math.max(0, Math.min(1, (v - 0.2) / 0.6));
		const r = Math.round(248 - t * 202);
		const g = Math.round(113 + t * 96);
		const b = Math.round(113 + t * 47);
		return `rgb(${r},${g},${b})`;
	}

	function knnScale(): [number, string][] {
		return [
			[0.2, 'rgb(248,113,113)'],
			[0.5, 'rgb(180,160,130)'],
			[0.8, 'rgb(46,209,160)']
		];
	}

	// ── Build Plotly traces ───────────────────────────────────────────────
	function buildPlot() {
		if (!Plotly || !mapData || !plotEl) return;

		const points = mapData.points;
		const { colors, legendGroups, colorbar, showLegend } = buildColors(points);
		const indexed = points.map((point, index) => ({ point, index }));
		const clustered = indexed.filter(({ point }) => !point.is_noise);
		const noise = indexed.filter(({ point }) => point.is_noise);
		const visibleClustered = clustered;
		const visibleNoise = showNoise ? noise : [];

		const getX = (p: TasteMapPoint) => (mode === '3d' ? p.x : p.x2);
		const getY = (p: TasteMapPoint) => (mode === '3d' ? p.y : p.y2);
		const getZ = (p: TasteMapPoint) => (mode === '3d' ? p.z : 0);

		const hoverText = (p: TasteMapPoint) =>
			`<b>${displayTitle(p)}</b><br>` +
			`${formatGenres(p.genres)}` +
			(p.year ? ` · ${p.year}` : '') +
			(p.aesthetic != null ? `<br>Aesthetic: ${p.aesthetic.toFixed(1)}` : '') +
			(p.colorfulness != null ? ` · Color: ${p.colorfulness.toFixed(0)}` : '') +
			`<br>Self k-NN: ${p.self_knn.toFixed(3)}`;

		function buildTrace(
			items: Array<{ point: TasteMapPoint; index: number }>,
			kind: 'main' | 'noise'
		): Record<string, unknown> | null {
			if (items.length === 0) return null;
			const trace: Record<string, unknown> = {
				x: items.map(({ point }) => getX(point)),
				y: items.map(({ point }) => getY(point)),
				customdata: items.map(({ index }) => index),
				text: items.map(({ point }) => hoverText(point)),
				hoverinfo: 'text',
				hoverlabel: {
					bgcolor: '#15171e',
					bordercolor: '#323744',
					font: { color: '#e8e9ef', size: 12, family: 'ui-sans-serif, system-ui, sans-serif' },
					align: 'left',
					namelength: -1
				},
				type: mode === '3d' ? 'scatter3d' : 'scattergl',
				mode: 'markers',
				showlegend: false
			};
			const mainColors = items.map(({ index }) => colors[index]);
			trace.marker = {
				size: kind === 'main' ? (mode === '3d' ? 5.5 : 8) : mode === '3d' ? 3 : 4.5,
				color: kind === 'main' ? mainColors : '#3f4452',
				opacity: kind === 'main' ? 0.9 : 0.26,
				line: {
					width: kind === 'main' ? (mode === '3d' ? 1.2 : 0.6) : 0,
					color: kind === 'main' ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0)'
				},
				symbol: 'circle'
			};
			if (mode === '3d') {
				trace.z = items.map(({ point }) => getZ(point));
			}
			if (kind === 'main' && colorbar) {
				const numericValues = items.map(({ point }) => {
					if (colorBy === 'aesthetic') return point.aesthetic ?? 0;
					if (colorBy === 'colorfulness') return point.colorfulness ?? 0;
					if (colorBy === 'year') return point.year ?? 2000;
					return point.self_knn;
				});
				trace.marker = {
					...(trace.marker as Record<string, unknown>),
					color: numericValues,
					colorscale: colorbar.colorscale,
					cmin: colorbar.colorscale[0][0],
					cmax: colorbar.colorscale[colorbar.colorscale.length - 1][0],
					showscale: mode !== '3d',
					colorbar: {
						title: colorbar.title,
						titleside: 'right',
						titlefont: { color: '#8a909f', size: 10 },
						tickfont: { color: '#5b6170', size: 9 },
						thickness: 12,
						len: 0.5,
						x: 1.01,
						outlinecolor: '#272b36',
						outlinewidth: 1,
						bgcolor: 'rgba(21,23,30,0.8)',
						colorscale: colorbar.colorscale
					}
				};
			}
			return trace;
		}

		const traces: Record<string, unknown>[] = [];
		const noiseTrace = buildTrace(visibleNoise, 'noise');
		const mainTrace = buildTrace(visibleClustered, 'main');
		if (noiseTrace) traces.push(noiseTrace);
		if (mainTrace) traces.push(mainTrace);
		if (traces.length === 0) {
			const fallback = buildTrace(noise, 'noise');
			if (fallback) traces.push(fallback);
		}

		// Add legend traces for cluster/genre modes
		if (showLegend && legendGroups.length > 0 && mode === '2d') {
			for (const g of legendGroups) {
				traces.push({
					x: [null],
					y: [null],
					type: 'scattergl',
					mode: 'markers',
					marker: { size: 9, color: g.color, opacity: 0.9 },
					name: g.name,
					showlegend: true
				});
			}
		}

		const layout: Record<string, unknown> = {
			paper_bgcolor: '#0c0d11',
			plot_bgcolor: '#0c0d11',
			font: { color: '#8a909f', size: 11, family: 'ui-sans-serif, system-ui, sans-serif' },
			margin: { l: 48, r: 24, t: 12, b: 40 },
			dragmode: mode === '3d' ? 'orbit' : 'pan',
			hovermode: 'closest',
			showlegend: showLegend && mode === '2d',
			legend: {
				x: 1.02,
				y: 1,
				xanchor: 'left',
				font: { color: '#8a909f', size: 11 },
				bgcolor: 'rgba(21,23,30,0.8)',
				bordercolor: '#272b36',
				borderwidth: 1
			},
			xaxis: {
				title: '',
				showgrid: true,
				gridcolor: '#1c1f28',
				zeroline: true,
				zerolinecolor: '#272b36',
				showticklabels: true,
				tickcolor: '#3f4452',
				tickfont: { color: '#5b6170', size: 10 }
			},
			yaxis: {
				title: '',
				showgrid: true,
				gridcolor: '#1c1f28',
				zeroline: true,
				zerolinecolor: '#272b36',
				showticklabels: true,
				tickcolor: '#3f4452',
				tickfont: { color: '#5b6170', size: 10 }
			}
		};

		if (mode === '3d') {
			layout.scene = {
				xaxis: {
					title: '',
					showgrid: true,
					gridcolor: '#1c1f28',
					zeroline: true,
					zerolinecolor: '#272b36',
					showticklabels: false,
					backgroundcolor: '#0c0d11'
				},
				yaxis: {
					title: '',
					showgrid: true,
					gridcolor: '#1c1f28',
					zeroline: true,
					zerolinecolor: '#272b36',
					showticklabels: false,
					backgroundcolor: '#0c0d11'
				},
				zaxis: {
					title: '',
					showgrid: true,
					gridcolor: '#1c1f28',
					zeroline: true,
					zerolinecolor: '#272b36',
					showticklabels: false,
					backgroundcolor: '#0c0d11'
				},
				bgcolor: '#0c0d11',
				camera: {
					eye: { x: 1.5, y: 1.5, z: 1.2 }
				}
			};
			delete layout.xaxis;
			delete layout.yaxis;
			layout.margin = { l: 0, r: 0, t: 0, b: 0 };
		}

		const config: Record<string, unknown> = {
			displaylogo: false,
			responsive: true,
			scrollZoom: true,
			displayModeBar: true,
			modeBarButtonsToRemove: [
				'toImage',
				'sendDataToCloud',
				'autoScale2d',
				'hoverClosestCartesian',
				'hoverCompareCartesian',
				'toggleSpikelines',
				'lasso2d',
				'select2d'
			],
			modeBarButtonsToAdd: mode === '3d' ? ['resetCameraDefault3d'] : ['resetScale2d']
		};

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		(Plotly as any).react(plotEl, traces, layout, config);
		const graph = plotEl as HTMLDivElement & {
			on?: (name: string, cb: (event: unknown) => void) => void;
			removeListener?: (name: string, cb: (event: unknown) => void) => void;
		};
		graph.removeListener?.('plotly_click', handleClick);
		graph.on?.('plotly_click', handleClick);
	}

	// ── Click handler ────────────────────────────────────────────────────
	function handleClick(event: unknown) {
		const e = event as { points?: Array<{ customdata?: number; pointIndex?: number }> };
		const idx = e.points?.[0]?.customdata ?? e.points?.[0]?.pointIndex;
		if (idx == null || !mapData) return;
		const point = mapData.points[idx];
		if (!point) return;
		selectedPoint = point;
		neighborsLoading = true;
		neighbors = [];
		getExemplarNeighbors(fetch, point.name, library)
			.then((r: { neighbors: TasteNeighbor[] }) => {
				neighbors = r.neighbors;
			})
			.catch(() => {
				toast('Could not load neighbors', 'bad');
			})
			.finally(() => {
				neighborsLoading = false;
			});
	}

	// ── Lifecycle ─────────────────────────────────────────────────────────
	onMount(async () => {
		const mod = await import('plotly.js-dist-min');
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		Plotly = (mod as any).default ?? (mod as any);

		if (mapData) buildPlot();

		const el = plotEl;
		if (el) {
			resizeObs = new ResizeObserver(() => {
				// The observer can fire once more while the node is being detached — on a
				// library switch, or when the map is collapsed. Plotly throws on a plot div
				// that is no longer displayed, so only resize one that is still laid out.
				if (!Plotly || !el.isConnected || !el.offsetParent) return;
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				(Plotly as any).Plots.resize(el);
			});
			resizeObs.observe(el);
		}
	});

	onDestroy(() => {
		resizeObs?.disconnect();
		const el = plotEl as
			| (HTMLDivElement & {
					removeListener?: (name: string, cb: (event: unknown) => void) => void;
			  })
			| null;
		el?.removeListener?.('plotly_click', handleClick);
		if (Plotly && plotEl)
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(Plotly as any).purge(plotEl);
	});

	// Rebuild on mode/colorBy/mapData changes
	$effect(() => {
		// Access reactive state to trigger the effect
		void mode;
		void colorBy;
		void mapData;
		void showNoise;
		if (Plotly && mapData) buildPlot();
	});
</script>

{#if loading}
	<div class="map-loading">
		<div class="spinner"></div>
		<span>Building taste map…</span>
	</div>
{:else if error}
	<div class="map-error">
		<span class="err-icon">!</span>
		<span>{error}</span>
	</div>
{:else if !mapData || mapData.points.length === 0}
	<div class="map-empty">
		<span>No taste profile data. Build a profile first.</span>
	</div>
{:else}
	<div class="map-wrap">
		<!-- Controls -->
		<div class="map-controls">
			<div class="control-group">
				<span class="ctrl-label">View</span>
				<div class="toggle-pair">
					<button class="toggle-btn" class:active={mode === '2d'} onclick={() => (mode = '2d')}>
						2D
					</button>
					<button class="toggle-btn" class:active={mode === '3d'} onclick={() => (mode = '3d')}>
						3D
					</button>
				</div>
			</div>
			<div class="control-group">
				<span class="ctrl-label">Color</span>
				<select class="color-select" bind:value={colorBy}>
					<option value="cluster">Cluster</option>
					<option value="genre">Genre</option>
					<option value="aesthetic">Aesthetic</option>
					<option value="colorfulness">Colorfulness</option>
					<option value="year">Year</option>
					<option value="self_knn">Self k-NN</option>
				</select>
			</div>
			<div class="control-group">
				<span class="ctrl-label">Noise</span>
				<div class="toggle-pair">
					<button class="toggle-btn" class:active={showNoise} onclick={() => (showNoise = true)}>
						Show
					</button>
					<button class="toggle-btn" class:active={!showNoise} onclick={() => (showNoise = false)}>
						Hide
					</button>
				</div>
			</div>
			<div class="map-meta">
				<span class="meta-badge">{mapData.projection.method.toUpperCase()}</span>
				<span class="meta-count">{mapData.summary.exemplars} exemplars</span>
				<span class="meta-count">{countOfSubjects(library, mapData.summary.unique_movies)}</span>
				<span class="meta-count">{mapData.summary.noise} noise</span>
				{#if mapData.clusters}
					<span class="meta-count">{mapData.clusters.length} clusters</span>
				{/if}
			</div>
		</div>

		{#if colorBy === 'genre' && mapData.points.every((p) => !p.genres || p.genres.length === 0)}
			<div class="map-warn">
				Genre metadata not available. Run <b>Enrich metadata</b> from the Key Art Engine page to populate
				genres from the database.
			</div>
		{/if}

		<div class="map-grid">
			<!-- Plot -->
			<div class="plot-container" bind:this={plotEl}></div>

			<!-- Side panel -->
			{#if selectedPoint}
				<div class="side-panel mq-rise">
					<button class="close-btn" onclick={() => (selectedPoint = null)}> &times; </button>
					{#if selectedPoint.thumb_url}
						<img src={selectedPoint.thumb_url} alt={selectedPoint.name} class="thumb" />
					{/if}
					<h3 class="p-name">{displayTitle(selectedPoint)}</h3>
					<div class="p-meta">
						<div class="p-row">
							<span class="p-label">Poster</span>
							<span class="p-val mono">{selectedPoint.name}</span>
						</div>
						{#if selectedPoint.genres}
							<div class="p-row">
								<span class="p-label">Genres</span>
								<span class="p-val">{formatGenres(selectedPoint.genres)}</span>
							</div>
						{/if}
						{#if selectedPoint.year}
							<div class="p-row">
								<span class="p-label">Year</span>
								<span class="p-val">{selectedPoint.year}</span>
							</div>
						{/if}
						{#if selectedPoint.aesthetic != null}
							<div class="p-row">
								<span class="p-label">Aesthetic</span>
								<span class="p-val mono">{selectedPoint.aesthetic.toFixed(1)}</span>
							</div>
						{/if}
						{#if selectedPoint.colorfulness != null}
							<div class="p-row">
								<span class="p-label">Colorfulness</span>
								<span class="p-val mono">{selectedPoint.colorfulness.toFixed(0)}</span>
							</div>
						{/if}
						<div class="p-row">
							<span class="p-label">Self k-NN</span>
							<span class="p-val mono">{selectedPoint.self_knn.toFixed(3)}</span>
						</div>
						{#if selectedPoint.movie_id != null}
							<div class="p-row">
								<span class="p-label">Film ID</span>
								<span class="p-val mono">{selectedPoint.movie_id}</span>
							</div>
						{/if}
						{#if selectedPoint.tmdb_id != null}
							<div class="p-row">
								<span class="p-label">TMDB</span>
								<span class="p-val mono">{selectedPoint.tmdb_id}</span>
							</div>
						{/if}
						{#if selectedPoint.is_noise}
							<div class="p-row">
								<span class="p-label">Clustering</span>
								<span class="p-val">Noise / outlier</span>
							</div>
						{/if}
						{#if selectedPoint.cluster != null && selectedPoint.cluster !== -1 && mapData?.clusters}
							{@const cluster = mapData.clusters.find(
								(c: { id: number; name: string }) => c.id === selectedPoint!.cluster
							)}
							{#if cluster}
								<div class="p-row">
									<span class="p-label">Cluster</span>
									<span class="p-val">{cluster.name}</span>
								</div>
							{/if}
						{/if}
					</div>

					{#if neighbors.length > 0}
						<div class="neighbors">
							<h4>Nearest exemplars</h4>
							{#each neighbors as n (n.name)}
								<div class="n-row">
									<span class="n-name">{formatName(n.name)}</span>
									<span class="n-sim mono">{n.similarity.toFixed(3)}</span>
								</div>
							{/each}
						</div>
					{:else if neighborsLoading}
						<div class="neighbors-loading">Loading…</div>
					{/if}
				</div>
			{:else}
				<div class="side-hint">
					<span>Click a point to explore</span>
				</div>
			{/if}
		</div>

		{#if mapData.note}
			<div class="map-note">{mapData.note}</div>
		{/if}
	</div>
{/if}

<style>
	.map-loading,
	.map-error,
	.map-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 12px;
		padding: 60px 24px;
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--faint);
		font-size: 13px;
		min-height: 300px;
	}

	.map-error {
		border-color: color-mix(in srgb, var(--bad) 35%, transparent);
		color: var(--bad);
	}
	.err-icon {
		width: 28px;
		height: 28px;
		border-radius: 50%;
		background: var(--bad);
		color: #fff;
		display: flex;
		align-items: center;
		justify-content: center;
		font-weight: 700;
		font-size: 15px;
	}

	.spinner {
		width: 24px;
		height: 24px;
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

	.map-wrap {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.map-controls {
		display: flex;
		align-items: center;
		gap: 20px;
		flex-wrap: wrap;
	}

	.control-group {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.ctrl-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}

	.toggle-pair {
		display: flex;
		border-radius: 8px;
		overflow: hidden;
		border: 1px solid var(--line2);
	}

	.toggle-btn {
		padding: 5px 14px;
		font-size: 12.5px;
		font-weight: 550;
		background: var(--panel2);
		color: var(--muted);
		border: none;
		cursor: pointer;
		transition: all 0.15s;
	}
	.toggle-btn.active {
		background: var(--gold-soft);
		color: var(--gold);
	}
	.toggle-btn:not(.active):hover {
		background: var(--panel);
		color: var(--text);
	}

	.color-select {
		padding: 5px 10px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12.5px;
		font-family: var(--font-sans);
		cursor: pointer;
		outline: none;
	}
	.color-select:focus {
		border-color: var(--gold-deep);
	}

	.map-meta {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-left: auto;
	}
	.meta-badge {
		font-family: var(--font-mono);
		font-size: 10px;
		padding: 2px 7px;
		border-radius: 4px;
		background: var(--panel2);
		color: var(--info);
		border: 1px solid color-mix(in srgb, var(--info) 25%, transparent);
	}
	.meta-count {
		font-size: 11px;
		color: var(--faint);
	}

	.map-grid {
		display: grid;
		grid-template-columns: 1fr 260px;
		gap: 12px;
		min-height: 500px;
	}

	.plot-container {
		width: 100%;
		min-height: 500px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--ink);
		overflow: hidden;
	}

	.side-hint {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100%;
		min-height: 200px;
		color: var(--faint2);
		font-size: 12px;
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
	}

	.side-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		overflow-y: auto;
		max-height: 500px;
		position: relative;
	}

	.close-btn {
		position: absolute;
		top: 6px;
		right: 10px;
		background: none;
		border: none;
		color: var(--faint);
		font-size: 20px;
		line-height: 1;
		padding: 2px 6px;
		border-radius: 4px;
	}
	.close-btn:hover {
		background: var(--panel2);
		color: var(--text);
	}

	.thumb {
		width: 100%;
		border-radius: 6px;
		border: 1px solid var(--line);
		object-fit: cover;
		max-height: 160px;
	}

	.p-name {
		margin: 0;
		font-size: 14px;
		font-weight: 650;
		color: var(--text);
		line-height: 1.3;
		text-transform: capitalize;
	}

	.p-meta {
		display: flex;
		flex-direction: column;
		gap: 5px;
	}

	.p-row {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 8px;
	}

	.p-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
		font-weight: 600;
		flex: none;
	}

	.p-val {
		font-size: 12px;
		color: var(--muted);
		text-align: right;
		text-transform: capitalize;
	}

	.neighbors {
		border-top: 1px solid var(--line);
		padding-top: 10px;
	}
	.neighbors h4 {
		margin: 0 0 6px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}

	.n-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 4px 0;
		gap: 8px;
	}

	.n-name {
		font-size: 12px;
		color: var(--muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		text-transform: capitalize;
	}

	.n-sim {
		font-size: 11px;
		color: var(--gold);
		flex: none;
	}

	.neighbors-loading {
		font-size: 11px;
		color: var(--faint);
		padding: 8px 0;
	}

	.map-warn {
		font-size: 11.5px;
		color: var(--warn);
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 20%, transparent);
		border-radius: var(--radius-sm);
		padding: 8px 12px;
		text-align: center;
	}
	.map-warn b {
		color: var(--text);
	}

	.map-note {
		font-size: 11.5px;
		color: var(--warn);
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 20%, transparent);
		border-radius: var(--radius-sm);
		padding: 8px 12px;
		text-align: center;
	}

	.mono {
		font-family: var(--font-mono);
	}

	:global(.plot-container .modebar) {
		background: rgba(21, 23, 30, 0.7) !important;
		border-radius: 6px !important;
	}
	:global(.plot-container .modebar-btn path) {
		fill: #5b6170 !important;
	}
	:global(.plot-container .modebar-btn:hover path) {
		fill: #e8e9ef !important;
	}
</style>
