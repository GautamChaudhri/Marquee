<script lang="ts">
	import type { PresentationValue } from '../types';

	let { value }: { value: PresentationValue | null | undefined } = $props();

	function duration(seconds: number): string {
		if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
		const minutes = Math.floor(seconds / 60);
		return `${minutes}m ${Math.round(seconds % 60)}s`;
	}

	function bytes(size: number): string {
		if (size < 1024) return `${size} B`;
		if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KiB`;
		if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MiB`;
		return `${(size / 1024 ** 3).toFixed(1)} GiB`;
	}
</script>

{#if !value}
	<span class="muted">—</span>
{:else if value.type === 'text'}
	<span>{value.text}</span>
{:else if value.type === 'number'}
	<span>{value.value}{value.unit ? ` ${value.unit}` : ''}</span>
{:else if value.type === 'duration'}
	<span>{duration(value.seconds)}</span>
{:else if value.type === 'bytes'}
	<span>{bytes(value.bytes)}</span>
{:else if value.type === 'timestamp'}
	<time datetime={value.at}>{new Date(value.at).toLocaleString()}</time>
{:else if value.type === 'boolean'}
	<span>{value.value ? 'Yes' : 'No'}</span>
{:else if value.type === 'badge'}
	<span class={`badge ${value.tone}`}>{value.text}</span>
{:else if value.type === 'subject'}
	<span>{value.display_name}</span>
{:else if value.type === 'link'}
	<a href={value.href}>{value.label}</a>
{/if}

<style>
	.muted {
		color: var(--muted);
	}
	a {
		color: var(--gold);
	}
	.badge {
		display: inline-flex;
		border: 1px solid var(--line2);
		border-radius: 999px;
		padding: 2px 7px;
		font-size: 11px;
	}
	.badge.positive {
		color: var(--good);
	}
	.badge.warning {
		color: var(--warn);
	}
	.badge.negative {
		color: var(--bad);
	}
</style>
