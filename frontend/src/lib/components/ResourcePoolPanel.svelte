<script lang="ts">
	import StatCard from './StatCard.svelte';
	import type { ResourcePoolStatus } from '$lib/api/jobs';

	let { resources }: { resources: ResourcePoolStatus[] } = $props();
</script>

<div class="grid">
	{#each resources as r (r.key)}
		{@const pct = r.capacity > 0 ? Math.round((r.in_use / r.capacity) * 100) : 0}
		<StatCard
			label={r.key}
			value={`${r.in_use}/${r.capacity}`}
			sub={!r.enabled ? 'disabled' : r.in_use >= r.capacity ? 'fully reserved' : 'available'}
			bar={pct}
			tone={!r.enabled ? 'muted' : r.in_use >= r.capacity ? 'bad' : 'good'}
		/>
	{/each}
</div>

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 10px;
	}
</style>
