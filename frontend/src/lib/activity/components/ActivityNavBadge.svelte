<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { getActivityAttention } from '../client';
	import type { ActivityAttentionResponse } from '../types';

	let summary = $state<ActivityAttentionResponse | null>(null);
	let controller: AbortController | null = null;
	let timer: ReturnType<typeof setInterval> | null = null;
	let inFlight = false;

	async function refresh(): Promise<void> {
		if (inFlight || document.hidden) return;
		inFlight = true;
		controller = new AbortController();
		try {
			summary = await getActivityAttention((input, init) =>
				fetch(input, { ...init, signal: controller?.signal })
			);
		} catch {
			// A navigation badge is advisory; preserve the last good aggregate.
		} finally {
			inFlight = false;
		}
	}

	onMount(() => {
		void refresh();
		timer = setInterval(() => void refresh(), 30_000);
	});

	onDestroy(() => {
		controller?.abort();
		if (timer) clearInterval(timer);
	});
</script>

{#if summary && summary.needs_attention > 0}
	<span
		class="badge"
		data-tone={summary.highest_severity}
		aria-label={`${summary.needs_attention} jobs need attention; highest severity ${summary.highest_severity}`}
		title={`${summary.needs_attention} need attention`}
	>
		<span aria-hidden="true">!</span>{summary.needs_attention}
	</span>
{/if}

<style>
	.badge {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		margin-left: auto;
		padding: 2px 5px;
		border: 1px solid color-mix(in srgb, var(--warn) 55%, var(--line));
		border-radius: 99px;
		color: var(--warn);
		font: 700 9px var(--font-mono);
	}
	.badge[data-tone='error'] {
		border-color: color-mix(in srgb, var(--bad) 55%, var(--line));
		color: var(--bad);
	}
</style>
