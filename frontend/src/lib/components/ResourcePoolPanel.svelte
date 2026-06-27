<script lang="ts">
	import type { ResourcePoolStatus } from '$lib/api/jobs';

	let { resources }: { resources: ResourcePoolStatus[] } = $props();

	const LABELS: Record<string, string> = {
		gpu: 'GPU',
		media_read: 'Media Read',
		media_write: 'Media Write',
		transcode: 'Transcode',
		network_external: 'External Network',
		maintenance_exclusive: 'Maintenance'
	};

	function resourceLabel(key: string): string {
		if (LABELS[key]) return LABELS[key];
		if (key.startsWith('media-file:')) return `File Lock ${key.slice('media-file:'.length)}`;
		return key.replace(/_/g, ' ');
	}

	function resourceTone(resource: ResourcePoolStatus): 'good' | 'warn' | 'bad' | 'muted' {
		if (!resource.enabled) return 'muted';
		if (resource.capacity <= 0) return 'muted';
		const ratio = resource.in_use / resource.capacity;
		if (ratio >= 1) return 'bad';
		if (ratio >= 0.7) return 'warn';
		return 'good';
	}

	const ordered = $derived.by(() => {
		const rank = ['gpu', 'media_read', 'media_write', 'transcode', 'network_external', 'maintenance_exclusive'];
		return [...resources].sort((a, b) => {
			const ai = rank.indexOf(a.key);
			const bi = rank.indexOf(b.key);
			if (ai !== bi) return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
			return a.key.localeCompare(b.key);
		});
	});

	const allIdle = $derived(ordered.length > 0 && ordered.every((resource) => resource.in_use === 0));
</script>

<div class="panel-box">
	{#if allIdle}
		<p class="summary">All resource pools are idle.</p>
	{/if}
	<div class="list">
		{#each ordered as resource (resource.key)}
			{@const tone = resourceTone(resource)}
			{@const pct = resource.capacity > 0 ? Math.min(100, (resource.in_use / resource.capacity) * 100) : 0}
			<div class="row">
				<div class="meta">
					<span class="label">{resourceLabel(resource.key)}</span>
					<span class="detail">{resource.key}</span>
				</div>
				<div class="bar-shell">
					<div class={`bar ${tone}`} style={`width: ${pct}%`} />
				</div>
				<span class="value mono">{resource.in_use}/{resource.capacity}</span>
			</div>
		{/each}
	</div>
</div>

<style>
	.panel-box {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 12px 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.summary {
		margin: 0;
		font-size: 12px;
		color: var(--muted);
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.row {
		display: grid;
		grid-template-columns: minmax(0, 160px) minmax(0, 1fr) auto;
		align-items: center;
		gap: 12px;
	}
	.meta {
		min-width: 0;
		display: flex;
		flex-direction: column;
	}
	.label {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--text);
	}
	.detail {
		font-size: 11px;
		color: var(--faint);
		font-family: var(--font-mono);
	}
	.bar-shell {
		height: 10px;
		border-radius: 999px;
		background: var(--panel2);
		border: 1px solid var(--line);
		overflow: hidden;
	}
	.bar {
		height: 100%;
		border-radius: inherit;
	}
	.bar.good {
		background: linear-gradient(90deg, color-mix(in srgb, var(--good) 82%, white), var(--good));
	}
	.bar.warn {
		background: linear-gradient(90deg, color-mix(in srgb, var(--warn) 82%, white), var(--warn));
	}
	.bar.bad {
		background: linear-gradient(90deg, color-mix(in srgb, var(--bad) 82%, white), var(--bad));
	}
	.bar.muted {
		background: var(--line2);
	}
	.value {
		font-size: 11.5px;
		color: var(--muted);
	}
	.mono {
		font-family: var(--font-mono);
	}
	@media (max-width: 720px) {
		.row {
			grid-template-columns: 1fr;
			gap: 6px;
		}
	}
</style>
