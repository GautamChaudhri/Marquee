<script lang="ts">
	import type { JobTimelineItem, SystemMetricsHistoryPoint } from '$lib/api/types';
	import JobOverlay from './JobOverlay.svelte';

	type MetricKey =
		| 'cpu_avg'
		| 'gpu_util'
		| 'gpu_mem'
		| 'gpu_enc'
		| 'gpu_dec'
		| 'ram_pct'
		| 'disk_read_bps'
		| 'disk_write_bps'
		| 'net_recv_bps'
		| 'net_sent_bps'
		| 'active_jobs';

	type Series = { key: MetricKey; label: string; color: string };

	let {
		title,
		points,
		series,
		jobs = [],
		yMax
	}: {
		title: string;
		points: SystemMetricsHistoryPoint[];
		series: Series[];
		jobs?: JobTimelineItem[];
		yMax?: number;
	} = $props();

	const width = 760;
	const height = 210;
	const padX = 16;
	const plotTop = 22;
	const plotHeight = 132;
	const plotWidth = width - padX * 2;
	const overlayY = plotTop + plotHeight + 12;
	const overlayHeight = 14;

	function numericValue(point: SystemMetricsHistoryPoint, key: MetricKey): number | null {
		const value = point[key];
		return typeof value === 'number' ? value : null;
	}

	const startAt = $derived(points.length ? new Date(points[0].ts).getTime() : 0);
	const endAt = $derived(points.length ? new Date(points[points.length - 1].ts).getTime() : 0);
	const derivedMax = $derived.by(() => {
		if (typeof yMax === 'number') return Math.max(1, yMax);
		let max = 0;
		for (const point of points) {
			for (const item of series) {
				const value = numericValue(point, item.key);
				if (value != null) max = Math.max(max, value);
			}
		}
		return Math.max(1, max);
	});

	function xFor(index: number): number {
		if (points.length <= 1) return padX;
		return padX + (index / (points.length - 1)) * plotWidth;
	}

	function yFor(value: number): number {
		const pct = Math.min(1, Math.max(0, value / derivedMax));
		return plotTop + plotHeight - pct * plotHeight;
	}

	function pathFor(key: MetricKey): string {
		let path = '';
		let drawing = false;
		points.forEach((point, index) => {
			const value = numericValue(point, key);
			if (value == null) {
				drawing = false;
				return;
			}
			const segment = `${xFor(index).toFixed(1)} ${yFor(value).toFixed(1)}`;
			path += `${drawing ? ' L ' : ' M '}${segment}`;
			drawing = true;
		});
		return path.trim();
	}

	function latest(key: MetricKey): number | null {
		for (let index = points.length - 1; index >= 0; index -= 1) {
			const value = numericValue(points[index], key);
			if (value != null) return value;
		}
		return null;
	}
</script>

<div class="chart-card">
	<div class="head">
		<span class="title">{title}</span>
		<div class="legend">
			{#each series as item (item.key)}
				<span class="legend-item">
					<span class="dot" style={`background:${item.color}`}></span>
					{item.label}
					{#if latest(item.key) != null}
						<strong>{Math.round(latest(item.key) ?? 0)}</strong>
					{/if}
				</span>
			{/each}
		</div>
	</div>
	{#if points.length === 0}
		<p class="empty">No history in this window yet.</p>
	{:else}
		<svg viewBox={`0 0 ${width} ${height}`} class="chart" role="img" aria-label={title}>
			<rect x={padX} y={plotTop} width={plotWidth} height={plotHeight} rx="12" class="plot-bg" />
			{#each [0.25, 0.5, 0.75, 1] as step (step)}
				<line
					x1={padX}
					x2={padX + plotWidth}
					y1={plotTop + plotHeight - plotHeight * step}
					y2={plotTop + plotHeight - plotHeight * step}
					class="grid-line"
				/>
			{/each}
			<JobOverlay
				{jobs}
				{startAt}
				{endAt}
				x={padX}
				y={overlayY}
				width={plotWidth}
				height={overlayHeight}
			/>
			{#each series as item (item.key)}
				{#if pathFor(item.key)}
					<path
						d={pathFor(item.key)}
						fill="none"
						stroke={item.color}
						stroke-width="3"
						stroke-linecap="round"
					/>
				{/if}
			{/each}
		</svg>
	{/if}
</div>

<style>
	.chart-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.head {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.title {
		font-size: 12.5px;
		font-weight: 650;
		color: var(--text);
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
	}
	.legend-item {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 11px;
		color: var(--muted);
	}
	.legend-item strong {
		color: var(--text);
		font-family: var(--font-mono);
		font-weight: 600;
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 999px;
	}
	.chart {
		width: 100%;
		height: auto;
	}
	.plot-bg {
		fill: color-mix(in srgb, var(--panel2) 85%, transparent);
		stroke: var(--line);
	}
	.grid-line {
		stroke: color-mix(in srgb, var(--line2) 70%, transparent);
		stroke-width: 1;
		stroke-dasharray: 4 6;
	}
	.empty {
		margin: 0;
		font-size: 12px;
		color: var(--muted);
	}
</style>
