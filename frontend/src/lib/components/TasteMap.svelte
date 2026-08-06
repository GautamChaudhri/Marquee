<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { theme } from '$lib/theme';
	import { pointTitle } from '$lib/taste/map-points';
	import type { ColorMode, ViewMode } from '$lib/taste/map-points';
	import type { TasteMapCluster, TasteMapPoint } from '$lib/api/types';

	let {
		points,
		clusters = null,
		mode = '2d',
		colorBy = 'cluster',
		showNoise = true,
		selected = null,
		onSelect
	}: {
		points: TasteMapPoint[];
		clusters?: TasteMapCluster[] | null;
		mode?: ViewMode;
		colorBy?: ColorMode;
		showNoise?: boolean;
		selected?: TasteMapPoint | null;
		onSelect: (point: TasteMapPoint | null) => void;
	} = $props();

	let plotEl = $state<HTMLDivElement | null>(null);
	let Plotly: typeof import('plotly.js-dist-min') | null = null;
	let resizeObs: ResizeObserver | null = null;

	// ── Palettes ──────────────────────────────────────────────────────────
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

	const UNCLASSIFIED = '#5b6170';

	/** Chart chrome follows the app theme rather than assuming a dark page. */
	const chrome = $derived(
		$theme === 'light'
			? {
					paper: '#ffffff',
					grid: '#e4e8ef',
					zero: '#ccd2dd',
					tick: '#5c6473',
					text: '#191d25',
					hover: '#ffffff',
					hoverLine: '#c6ccd7',
					noise: '#aab1bf',
					markerEdge: 'rgba(0,0,0,0.16)'
				}
			: {
					paper: '#0c0d11',
					grid: '#1c1f28',
					zero: '#272b36',
					tick: '#5b6170',
					text: '#e8e9ef',
					hover: '#15171e',
					hoverLine: '#323744',
					noise: '#3f4452',
					markerEdge: 'rgba(255,255,255,0.12)'
				}
	);

	// Plotly's `react` receives a fresh layout whenever a selected point, colour, or
	// theme changes. Keep the UI revision stable for one projection so Plotly retains
	// a user's pan/zoom in 2D and camera orbit in 3D across those updates.
	const viewRevision = $derived.by(
		() =>
			`${mode}:${points
				.map((point) =>
					[point.name, point.asset_kind, point.x, point.y, point.z, point.x2, point.y2].join(':')
				)
				.join('|')}`
	);

	function genreColor(genres: string[] | null): string {
		if (!genres || genres.length === 0) return UNCLASSIFIED;
		for (const g of genres) if (GENRE_COLORS[g]) return GENRE_COLORS[g];
		return UNCLASSIFIED;
	}

	function buildColors(rows: TasteMapPoint[]): {
		colors: string[];
		legend: { name: string; color: string }[];
		colorbar?: { title: string; colorscale: [number, string][] };
	} {
		if (colorBy === 'cluster') {
			const colorById: Record<number, string> = {};
			const nameById: Record<number, string> = {};
			for (const c of clusters ?? []) {
				colorById[c.id] = CLUSTER_COLORS[c.id % CLUSTER_COLORS.length];
				nameById[c.id] = c.name;
			}
			const noiseCount = rows.filter((p) => p.cluster == null || p.cluster === -1).length;
			const colors = rows.map((p) =>
				p.cluster != null && p.cluster !== -1
					? (colorById[p.cluster] ?? chrome.noise)
					: chrome.noise
			);
			const legend = Object.entries(colorById)
				.map(([id, color]) => ({ id: Number(id), color }))
				.filter(({ id }) => Boolean(nameById[id]))
				.map(({ id, color }) => ({ name: nameById[id], color }));
			if (noiseCount > 0) legend.push({ name: `Unclustered (${noiseCount})`, color: chrome.noise });
			return { colors, legend };
		}

		if (colorBy === 'genre') {
			const colors = rows.map((p) => genreColor(p.genres));
			const present = [...new Set(rows.map((p) => p.genres?.[0] ?? 'unknown'))];
			return {
				colors,
				legend: present
					.filter((g) => GENRE_COLORS[g])
					.map((g) => ({ name: g, color: GENRE_COLORS[g] }))
			};
		}

		const scales: Record<string, [number, string][]> = {
			aesthetic: aestheticScale(),
			colorfulness: colorfulnessScale(),
			year: yearScale(),
			self_knn: knnScale()
		};
		const titles: Record<string, string> = {
			aesthetic: 'Aesthetic',
			colorfulness: 'Colorfulness',
			year: 'Year',
			self_knn: 'Self k-NN'
		};
		return {
			colors: rows.map(() => CLUSTER_COLORS[0]),
			legend: [],
			colorbar: { title: titles[colorBy], colorscale: scales[colorBy] }
		};
	}

	function numericValue(point: TasteMapPoint): number {
		if (colorBy === 'aesthetic') return point.aesthetic ?? 0;
		if (colorBy === 'colorfulness') return point.colorfulness ?? 0;
		if (colorBy === 'year') return point.year ?? 2000;
		return point.self_knn;
	}

	function aestheticColor(v: number): string {
		const t = Math.max(0, Math.min(1, (v - 2) / 7));
		return `rgb(${Math.round(56 + t * 199)},${Math.round(160 + t * 34)},${Math.round(251 - t * 176)})`;
	}

	function aestheticScale(): [number, string][] {
		return Array.from({ length: 11 }, (_, i) => {
			const v = 2 + (i / 10) * 7;
			return [v, aestheticColor(v)] as [number, string];
		});
	}

	function colorfulnessScale(): [number, string][] {
		return [
			[0, 'rgb(90,140,240)'],
			[75, 'rgb(180,170,140)'],
			[150, 'rgb(255,194,75)']
		];
	}

	function yearScale(): [number, string][] {
		return [
			[1920, 'rgb(100,150,240)'],
			[1975, 'rgb(160,170,170)'],
			[2030, 'rgb(255,194,100)']
		];
	}

	function knnScale(): [number, string][] {
		return [
			[0.2, 'rgb(248,113,113)'],
			[0.5, 'rgb(180,160,130)'],
			[0.8, 'rgb(46,209,160)']
		];
	}

	// ── Traces ────────────────────────────────────────────────────────────
	function buildPlot() {
		if (!Plotly || !plotEl) return;

		const { colors, legend, colorbar } = buildColors(points);
		const indexed = points.map((point, index) => ({ point, index }));
		const clustered = indexed.filter(({ point }) => !point.is_noise);
		const noise = showNoise ? indexed.filter(({ point }) => point.is_noise) : [];

		const getX = (p: TasteMapPoint) => (mode === '3d' ? p.x : p.x2);
		const getY = (p: TasteMapPoint) => (mode === '3d' ? p.y : p.y2);

		const hoverText = (p: TasteMapPoint) =>
			`<b>${pointTitle(p)}</b>` +
			(p.genres?.length ? `<br>${p.genres.join(', ')}` : '') +
			(p.year ? `${p.genres?.length ? ' · ' : '<br>'}${p.year}` : '');

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
					bgcolor: chrome.hover,
					bordercolor: chrome.hoverLine,
					font: { color: chrome.text, size: 12, family: 'ui-sans-serif, system-ui, sans-serif' },
					align: 'left',
					namelength: -1
				},
				type: mode === '3d' ? 'scatter3d' : 'scattergl',
				mode: 'markers',
				showlegend: false
			};
			trace.marker = {
				size: kind === 'main' ? (mode === '3d' ? 5.5 : 8) : mode === '3d' ? 3 : 4.5,
				color: kind === 'main' ? items.map(({ index }) => colors[index]) : chrome.noise,
				opacity: kind === 'main' ? 0.9 : 0.26,
				line: {
					width: kind === 'main' ? (mode === '3d' ? 1.2 : 0.6) : 0,
					color: kind === 'main' ? chrome.markerEdge : 'rgba(0,0,0,0)'
				},
				symbol: 'circle'
			};
			if (mode === '3d') trace.z = items.map(({ point }) => point.z);
			if (kind === 'main' && colorbar) {
				trace.marker = {
					...(trace.marker as Record<string, unknown>),
					color: items.map(({ point }) => numericValue(point)),
					colorscale: colorbar.colorscale,
					cmin: colorbar.colorscale[0][0],
					cmax: colorbar.colorscale[colorbar.colorscale.length - 1][0],
					showscale: mode !== '3d',
					colorbar: {
						title: colorbar.title,
						titleside: 'right',
						titlefont: { color: chrome.tick, size: 10 },
						tickfont: { color: chrome.tick, size: 9 },
						thickness: 12,
						len: 0.5,
						x: 1.01,
						outlinecolor: chrome.grid,
						outlinewidth: 1,
						bgcolor: 'rgba(0,0,0,0)'
					}
				};
			}
			return trace;
		}

		const traces: Record<string, unknown>[] = [];
		const noiseTrace = buildTrace(noise, 'noise');
		const mainTrace = buildTrace(clustered, 'main');
		if (noiseTrace) traces.push(noiseTrace);
		if (mainTrace) traces.push(mainTrace);
		if (traces.length === 0) {
			const fallback = buildTrace(
				indexed.filter(({ point }) => point.is_noise),
				'noise'
			);
			if (fallback) traces.push(fallback);
		}

		// The marker that shows which point the detail panel is describing.
		if (selected) {
			traces.push({
				x: [mode === '3d' ? selected.x : selected.x2],
				y: [mode === '3d' ? selected.y : selected.y2],
				...(mode === '3d' ? { z: [selected.z] } : {}),
				type: mode === '3d' ? 'scatter3d' : 'scattergl',
				mode: 'markers',
				hoverinfo: 'skip',
				showlegend: false,
				marker: {
					size: mode === '3d' ? 10 : 16,
					color: 'rgba(0,0,0,0)',
					line: { width: 2, color: '#ffc24b' }
				}
			});
		}

		const showLegend = legend.length > 0 && mode === '2d';
		if (showLegend) {
			for (const entry of legend) {
				traces.push({
					x: [null],
					y: [null],
					type: 'scattergl',
					mode: 'markers',
					marker: { size: 9, color: entry.color, opacity: 0.9 },
					name: entry.name,
					showlegend: true
				});
			}
		}

		const axis = {
			title: '',
			showgrid: true,
			gridcolor: chrome.grid,
			zeroline: true,
			zerolinecolor: chrome.zero,
			showticklabels: true,
			tickcolor: chrome.zero,
			tickfont: { color: chrome.tick, size: 10 }
		};

		const layout: Record<string, unknown> = {
			paper_bgcolor: chrome.paper,
			plot_bgcolor: chrome.paper,
			font: { color: chrome.tick, size: 11, family: 'ui-sans-serif, system-ui, sans-serif' },
			uirevision: viewRevision,
			margin: { l: 44, r: 20, t: 10, b: 34 },
			dragmode: mode === '3d' ? 'orbit' : 'pan',
			hovermode: 'closest',
			showlegend: showLegend,
			legend: {
				x: 1.02,
				y: 1,
				xanchor: 'left',
				font: { color: chrome.tick, size: 11 },
				bgcolor: 'rgba(0,0,0,0)',
				bordercolor: chrome.grid,
				borderwidth: 1
			},
			xaxis: axis,
			yaxis: axis
		};

		if (mode === '3d') {
			const sceneAxis = { ...axis, showticklabels: false, backgroundcolor: chrome.paper };
			layout.scene = {
				uirevision: viewRevision,
				xaxis: sceneAxis,
				yaxis: sceneAxis,
				zaxis: sceneAxis,
				bgcolor: chrome.paper,
				camera: { eye: { x: 1.5, y: 1.5, z: 1.2 } }
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

	function handleClick(event: unknown) {
		const e = event as { points?: Array<{ customdata?: number; pointIndex?: number }> };
		const idx = e.points?.[0]?.customdata ?? e.points?.[0]?.pointIndex;
		if (idx == null) return;
		const point = points[idx];
		if (point) onSelect(point);
	}

	onMount(async () => {
		const mod = await import('plotly.js-dist-min');
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		Plotly = (mod as any).default ?? (mod as any);
		buildPlot();

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
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		if (Plotly && plotEl) (Plotly as any).purge(plotEl);
	});

	$effect(() => {
		// Access reactive state to trigger the effect.
		void mode;
		void colorBy;
		void showNoise;
		void points;
		void selected;
		void chrome;
		if (Plotly) buildPlot();
	});
</script>

<div class="plot" bind:this={plotEl}></div>

<style>
	.plot {
		width: 100%;
		height: 100%;
		min-height: 320px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--ink);
		overflow: hidden;
	}
	:global(.plot .modebar) {
		background: transparent !important;
	}
	:global(.plot .modebar-btn path) {
		fill: var(--faint) !important;
	}
	:global(.plot .modebar-btn:hover path) {
		fill: var(--text) !important;
	}
</style>
