<script lang="ts">
	import StatCard from './StatCard.svelte';
	import ProgressBar from './ProgressBar.svelte';
	import MetricsChart from './MetricsChart.svelte';
	import { bytesH } from '$lib/display';
	import type { SystemMetrics, SystemMetricsHistory } from '$lib/api/types';

	let {
		metrics,
		history,
		window,
		onWindowChange
	}: {
		metrics: SystemMetrics;
		history: SystemMetricsHistory | null;
		window: '15m' | '1h' | '6h' | '24h';
		onWindowChange?: (window: '15m' | '1h' | '6h' | '24h') => void;
	} = $props();

	const WINDOW_OPTIONS: Array<'15m' | '1h' | '6h' | '24h'> = ['15m', '1h', '6h', '24h'];
	const computeSeries = $derived([
		{ key: 'cpu_avg', label: 'CPU', color: 'var(--cpu)' },
		{ key: 'ram_pct', label: 'RAM', color: 'var(--accent)' },
		...(metrics.gpu ? [{ key: 'gpu_util', label: 'GPU', color: 'var(--gpu)' }] : [])
	]);
	const gpuSeries = [
		{ key: 'gpu_mem', label: 'Memory Ctrl', color: '#60a5fa' },
		{ key: 'gpu_enc', label: 'Encode', color: '#f59e0b' },
		{ key: 'gpu_dec', label: 'Decode', color: '#10b981' }
	];
	const diskSeries = [
		{ key: 'disk_read_bps', label: 'Read', color: '#38bdf8' },
		{ key: 'disk_write_bps', label: 'Write', color: '#fb7185' }
	];
	const netSeries = [
		{ key: 'net_recv_bps', label: 'Receive', color: '#34d399' },
		{ key: 'net_sent_bps', label: 'Send', color: '#f97316' }
	];

	let prev: { disk: SystemMetrics['disk']; net: SystemMetrics['net']; at: number } | null = null;
	let diskReadRate = $state<number | null>(null);
	let diskWriteRate = $state<number | null>(null);
	let netSentRate = $state<number | null>(null);
	let netRecvRate = $state<number | null>(null);

	function rateOf(curr: number | null, prior: number | null, elapsedSeconds: number): number | null {
		if (curr == null || prior == null || elapsedSeconds <= 0 || curr < prior) return null;
		return (curr - prior) / elapsedSeconds;
	}

	$effect(() => {
		const now = Date.now();
		if (prev) {
			const elapsed = (now - prev.at) / 1000;
			diskReadRate = rateOf(metrics.disk.readBytes, prev.disk.readBytes, elapsed);
			diskWriteRate = rateOf(metrics.disk.writeBytes, prev.disk.writeBytes, elapsed);
			netSentRate = rateOf(metrics.net.bytesSent, prev.net.bytesSent, elapsed);
			netRecvRate = rateOf(metrics.net.bytesRecv, prev.net.bytesRecv, elapsed);
		}
		prev = { disk: metrics.disk, net: metrics.net, at: now };
	});

	function perSec(rate: number | null): string {
		return rate == null ? '—' : `${bytesH(rate)}/s`;
	}
</script>

<div class="stack">
	<div class="grid">
		<div class="panel">
			<div class="panel-head">
				<span class="panel-title">{metrics.cpu.model}</span>
				<span class="panel-sub mono">{metrics.cpu.cores}C / {metrics.cpu.threads}T</span>
			</div>
			<StatCard
				label="CPU"
				value={`${metrics.cpu.avg.toFixed(0)}%`}
				sub={[
					metrics.cpu.freq ? `${metrics.cpu.freq} MHz` : null,
					metrics.cpu.load != null ? `load ${metrics.cpu.load}` : null,
					metrics.cpu.temp != null ? `${metrics.cpu.temp}°C` : null
				]
					.filter(Boolean)
					.join(' · ')}
				bar={metrics.cpu.avg}
				tone={metrics.cpu.avg > 85 ? 'bad' : metrics.cpu.avg > 60 ? 'warn' : 'good'}
			/>
			{#if metrics.cpu.perCore?.length}
				<div class="cores">
					{#each metrics.cpu.perCore as pct, i (i)}
						<div
							class="core"
							title={`core ${i}: ${pct.toFixed(0)}%`}
							style={`--fill: ${Math.min(100, Math.max(0, pct)) / 100}`}
						>
							{pct.toFixed(0)}
						</div>
					{/each}
				</div>
			{/if}
		</div>

		{#if metrics.gpu}
			<div class="panel">
				<div class="panel-head">
					<span class="panel-title">{metrics.gpu.model}</span>
					{#if metrics.gpu.power != null}<span class="panel-sub mono">{metrics.gpu.power} W</span>{/if}
				</div>
				<div class="dual">
					<StatCard
						label="GPU util"
						value={`${metrics.gpu.util}%`}
						sub={metrics.gpu.temp != null ? `${metrics.gpu.temp}°C` : undefined}
						bar={metrics.gpu.util}
						tone={metrics.gpu.util > 85 ? 'bad' : metrics.gpu.util > 60 ? 'warn' : 'good'}
					/>
					<StatCard
						label="Memory controller"
						value={`${metrics.gpu.memUtil}%`}
						sub={`ENC ${metrics.gpu.enc ?? '—'}% · DEC ${metrics.gpu.dec ?? '—'}%`}
						bar={metrics.gpu.memUtil}
						tone="info"
					/>
				</div>
				<div class="vram">
					<span>VRAM {bytesH(metrics.gpu.vramUsed)} / {bytesH(metrics.gpu.vramTotal)}</span>
					<ProgressBar
						value={(metrics.gpu.vramUsed / metrics.gpu.vramTotal) * 100}
						tone="gpu"
						height={6}
					/>
				</div>
			</div>
		{/if}

		<StatCard label="RAM" value={`${metrics.ram.pct.toFixed(0)}%`} sub={`${bytesH(metrics.ram.used)} / ${bytesH(metrics.ram.total)}`} bar={metrics.ram.pct} />

		<StatCard
			label="Disk"
			value={metrics.disk.pct != null ? `${metrics.disk.pct.toFixed(0)}%` : '—'}
			sub={`read ${perSec(diskReadRate)} · write ${perSec(diskWriteRate)}`}
			bar={metrics.disk.pct ?? undefined}
		/>

		<StatCard
			label="Network"
			value={perSec(netRecvRate)}
			sub={`↓ ${perSec(netRecvRate)} · ↑ ${perSec(netSentRate)}`}
			tone="info"
		/>

		<StatCard label="Uptime" value={metrics.uptime} tone="muted" />
	</div>

	<div class="history-head">
		<div>
			<h3>Metrics History</h3>
			<p>Jobs overlayed on the charts are clickable.</p>
		</div>
		<div class="window-picker">
			{#each WINDOW_OPTIONS as option (option)}
				<button class:active={window === option} onclick={() => onWindowChange?.(option)}>{option}</button>
			{/each}
		</div>
	</div>

	{#if history}
		<div class="chart-grid">
			<MetricsChart
				title="Compute Load"
				points={history.points}
				jobs={history.jobs}
				yMax={100}
				series={computeSeries}
			/>
			{#if metrics.gpu}
				<MetricsChart
					title="GPU Media Engines"
					points={history.points}
					jobs={history.jobs}
					yMax={100}
					series={gpuSeries}
				/>
			{/if}
			<MetricsChart
				title="Disk Throughput"
				points={history.points}
				jobs={history.jobs}
				series={diskSeries}
			/>
			<MetricsChart
				title="Network Throughput"
				points={history.points}
				jobs={history.jobs}
				series={netSeries}
			/>
		</div>
	{/if}
</div>

<style>
	.stack {
		display: flex;
		flex-direction: column;
		gap: 18px;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
		gap: 12px;
		align-items: start;
	}
	.panel {
		grid-column: span 2;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		min-width: 280px;
	}
	.panel-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 10px;
	}
	.panel-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.panel-sub {
		font-size: 11px;
		color: var(--faint);
		flex: none;
	}
	.dual {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 10px;
	}
	.vram {
		display: flex;
		flex-direction: column;
		gap: 6px;
		font-size: 11.5px;
		color: var(--muted);
	}
	.cores {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(28px, 1fr));
		gap: 4px;
	}
	.core {
		aspect-ratio: 1;
		display: grid;
		place-items: center;
		border-radius: 5px;
		font-size: 9px;
		font-family: var(--font-mono);
		color: var(--text);
		background: color-mix(in srgb, var(--cpu) calc(var(--fill) * 80%), var(--panel2));
		border: 1px solid var(--line);
	}
	.history-head {
		display: flex;
		justify-content: space-between;
		align-items: end;
		gap: 12px;
		flex-wrap: wrap;
	}
	.history-head h3,
	.history-head p {
		margin: 0;
	}
	.history-head h3 {
		font-size: 13px;
		font-weight: 650;
		color: var(--text);
	}
	.history-head p {
		font-size: 12px;
		color: var(--muted);
	}
	.window-picker {
		display: inline-flex;
		gap: 6px;
	}
	.window-picker button {
		padding: 6px 10px;
		border-radius: 999px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		font-size: 11.5px;
	}
	.window-picker button.active {
		background: var(--gold-soft);
		border-color: var(--gold-deep);
		color: var(--gold);
	}
	.chart-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
		gap: 12px;
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
