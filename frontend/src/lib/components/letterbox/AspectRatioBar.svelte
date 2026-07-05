<script lang="ts">
	let { distribution }: { distribution: Record<string, number> } = $props();

	const entries = $derived(
		Object.entries(distribution || {})
			.map(([label, count]) => ({ label, count }))
			.sort((a, b) => b.count - a.count)
	);

	const total = $derived(entries.reduce((sum, e) => sum + e.count, 0));

	function pct(count: number): string {
		if (total <= 0) return '0%';
		return `${((count / total) * 100).toFixed(1)}%`;
	}

	const COLORS: Record<string, string> = {
		'2.39:1': 'var(--good)',
		'2.35:1': 'color-mix(in srgb, var(--good) 80%, white)',
		'1.85:1': 'var(--info)',
		'16:9': 'var(--gold)',
		'4:3': 'var(--bad)',
		other: 'var(--muted)'
	};

	function colorFor(label: string): string {
		if (COLORS[label]) return COLORS[label];
		if (label.includes('2.39') || label.includes('2.40')) return 'var(--good)';
		if (label.includes('1.85')) return 'var(--info)';
		if (label.includes('1.78') || label.includes('16:9')) return 'var(--gold)';
		if (label.includes('1.33') || label.includes('4:3')) return 'var(--bad)';
		return 'var(--low)';
	}
</script>

<div class="ar-bar-container">
	<div class="bar">
		{#each entries as entry (entry.label)}
			{#if entry.count > 0}
				<span
					class="seg"
					style={`width: ${pct(entry.count)}; background: ${colorFor(entry.label)}`}
					title={`${entry.label}: ${entry.count} (${pct(entry.count)})`}
				></span>
			{/if}
		{/each}
	</div>
	<div class="ar-legend">
		{#each entries as entry (entry.label)}
			{#if entry.count > 0}
				<div class="legend-item">
					<span class="color-box" style={`background: ${colorFor(entry.label)}`}></span>
					<span class="label">{entry.label}</span>
					<span class="count">{entry.count}</span>
					<span class="pct">({pct(entry.count)})</span>
				</div>
			{/if}
		{/each}
	</div>
</div>

<style>
	.ar-bar-container {
		display: flex;
		flex-direction: column;
		gap: 12px;
		width: 100%;
	}
	.bar {
		display: flex;
		height: 12px;
		border-radius: 6px;
		overflow: hidden;
		background: var(--ink3);
		width: 100%;
	}
	.seg {
		display: block;
		height: 100%;
		transition: width 0.3s ease;
	}
	.ar-legend {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
		gap: 8px;
		margin-top: 4px;
	}
	.legend-item {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 11px;
	}
	.color-box {
		width: 10px;
		height: 10px;
		border-radius: 2px;
		flex-shrink: 0;
	}
	.label {
		font-weight: 600;
		color: var(--text);
		white-space: nowrap;
	}
	.count {
		color: var(--muted);
	}
	.pct {
		color: var(--faint2);
	}
</style>
