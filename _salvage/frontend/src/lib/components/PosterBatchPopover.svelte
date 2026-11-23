<script lang="ts">
	import PosterBatchModeControl from './PosterBatchModeControl.svelte';
	import type { PosterBatchMode } from '$lib/api/types';

	let {
		mode = $bindable('chunked'),
		chunkSize = $bindable(8),
		disabled = false
	}: {
		mode?: PosterBatchMode;
		chunkSize?: number;
		disabled?: boolean;
	} = $props();

	// Open state is read off the element rather than mirrored into a rune: <details> is
	// opened by the browser itself when the summary is activated, so the DOM is the
	// authority and a mirrored copy can only disagree with it.
	let root = $state<HTMLDetailsElement | null>(null);

	const summaryLabel = $derived(mode === 'chunked' ? `Chunks of ${chunkSize}` : 'Unified');

	function onKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape' || !root?.open) return;
		root.open = false;
		// Escape must not drop focus onto <body> — hand it back to the control that opened.
		root.querySelector('summary')?.focus();
	}

	// `pointerdown` rather than `click`, and deliberately without preventDefault: a click on
	// a toolbar button beside this one should both dismiss the panel and fire the button.
	function onPointerDown(event: PointerEvent) {
		if (!root?.open) return;
		if (!root.contains(event.target as Node)) root.open = false;
	}
</script>

<svelte:window onkeydown={onKeydown} onpointerdown={onPointerDown} />

<details class="batch-popover" bind:this={root}>
	<summary class="pill quiet" data-testid="batch-trigger">
		Batching · {summaryLabel}
		<span class="chev" aria-hidden="true">▾</span>
	</summary>
	<div class="panel">
		<PosterBatchModeControl bind:mode bind:chunkSize {disabled} />
	</div>
</details>

<style>
	.batch-popover {
		position: relative;
	}
	summary {
		list-style: none;
		cursor: pointer;
		user-select: none;
	}
	summary::marker,
	summary::-webkit-details-marker {
		display: none;
		content: '';
	}
	summary:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	/* Above the activity panel that renders directly below the toolbar. */
	.panel {
		position: absolute;
		z-index: 20;
		left: 0;
		top: calc(100% + 6px);
		width: min(320px, calc(100vw - 32px));
		padding: 12px;
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		background: var(--panel2);
		box-shadow: 0 16px 36px var(--shadow);
	}
	/* The control ships its own card chrome for standalone use; inside the panel it is
	   just the contents, stacked. */
	.panel :global(.batch-control) {
		flex-direction: column;
		align-items: flex-start;
		gap: 10px;
		min-height: 0;
		padding: 0;
		border: 0;
		background: transparent;
	}
</style>
