<script lang="ts">
	import type { Snippet } from 'svelte';

	/** An empty, loading, or failed state, in words, with an optional action. */
	let {
		tone = 'info',
		message,
		action
	}: { tone?: 'info' | 'bad' | 'loading'; message: string; action?: Snippet } = $props();
</script>

<div class="notice {tone}" role={tone === 'bad' ? 'alert' : 'status'}>
	<span>{message}</span>
	{#if action}{@render action()}{/if}
</div>

<style>
	.notice {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 10px 14px;
		padding: 12px 14px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel);
		color: var(--muted);
		font-size: 13px;
	}
	.bad {
		border-color: color-mix(in srgb, var(--bad) 45%, transparent);
		color: var(--bad);
	}
	.loading span {
		animation: mq-pulse 1.4s ease-in-out infinite;
	}
</style>
