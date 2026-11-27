<script lang="ts">
	import { untrack } from 'svelte';
	import { formatScore } from '#lib/format.ts';
	import Icon from '#lib/ui/Icon.svelte';
	import Modal from '#lib/ui/Modal.svelte';
	import type { Closeup } from './panel';

	/** One poster as large as the window allows, with nothing else on screen. It shows the
	 *  run's large copy when it kept one, else the 500 px file, and says under it how many
	 *  pixels the file shown has. The arrows, on screen and on the keyboard, step through the
	 *  other variants of its design. */
	let {
		items,
		start,
		title,
		onClose
	}: {
		items: Closeup[];
		/** The candidate to open on. */
		start: number;
		/** What the posters are, e.g. "Design 4". */
		title: string;
		onClose: () => void;
	} = $props();

	let at = $state(
		untrack(() =>
			Math.max(
				0,
				items.findIndex((item) => item.id === start)
			)
		)
	);
	const item = $derived(items[Math.min(at, items.length - 1)]);
	const many = $derived(items.length > 1);

	// The size of the file on screen, known once it has loaded; it belongs to one poster.
	let loaded = $state<{ id: number; width: number; height: number } | null>(null);
	const resolution = $derived(
		item && loaded?.id === item.id ? `${loaded.width} × ${loaded.height} px` : null
	);

	function measure(id: number, e: Event) {
		const image = e.currentTarget as HTMLImageElement;
		if (image.naturalWidth > 0) {
			loaded = { id, width: image.naturalWidth, height: image.naturalHeight };
		}
	}

	function step(by: number) {
		at = (at + by + items.length) % items.length;
	}

	function onKey(e: KeyboardEvent) {
		if (!many) return;
		if (e.key === 'ArrowLeft') step(-1);
		else if (e.key === 'ArrowRight') step(1);
	}
</script>

<svelte:window onkeydown={onKey} />

{#if item}
	<Modal label="{title}, a closer look" {onClose} bare testid="lightbox">
		<div class="stage">
			<button class="close" onclick={onClose} aria-label="Close"><Icon name="x" size={20} /></button
			>
			{#if many}
				<button class="nav prev" onclick={() => step(-1)} aria-label="Previous variant">
					<Icon name="chevron-left" size={22} />
				</button>
			{/if}
			<!-- A click beside the poster closes the view, as a click on the backdrop does. -->
			<div
				class="picture"
				role="button"
				tabindex="-1"
				aria-label="Dismiss"
				onclick={(e) => e.target === e.currentTarget && onClose()}
				onkeydown={(e) => e.target === e.currentTarget && e.key === 'Enter' && onClose()}
			>
				{#key item.id}
					<img
						src={item.src}
						alt="{title}{many ? `, variant ${item.place}` : ''}, large"
						data-testid="lightbox-image"
						data-candidate={item.id}
						onload={(e) => measure(item.id, e)}
					/>
				{/key}
			</div>
			{#if many}
				<button class="nav next" onclick={() => step(1)} aria-label="Next variant">
					<Icon name="chevron" size={22} />
				</button>
			{/if}
			<p class="caption" role="status" data-testid="lightbox-caption">
				<b>{title}{many ? ` · variant ${item.place} of ${item.of}` : ''}</b>
				· score {formatScore(item.score)}{resolution ? ` · ${resolution}` : ''}
			</p>
		</div>
	</Modal>
{/if}

<style>
	.stage {
		position: relative;
		flex: 1;
		min-height: 0;
		display: grid;
		grid-template-columns: 64px minmax(0, 1fr) 64px;
		grid-template-rows: minmax(0, 1fr) auto;
		align-items: center;
		padding: 16px 0 10px;
	}
	/* The cell's size comes from the window alone, never from the picture in it: left to
	   itself it grows to the file's own height, and a 1500 px copy runs off the screen. */
	.picture {
		grid-column: 2;
		grid-row: 1;
		height: 100%;
		min-height: 0;
		min-width: 0;
		container-type: size;
		display: grid;
		place-items: center;
		cursor: default;
	}
	/* A poster's shape, as tall as the cell or as wide as the cell, whichever comes first,
	   whatever the file's own size: a large copy is scaled down to fit, a 500 px file up. */
	img {
		display: block;
		height: min(100cqh, 150cqw);
		width: auto;
		aspect-ratio: 2 / 3;
		object-fit: contain;
		border-radius: var(--radius-sm);
	}
	.nav,
	.close {
		display: grid;
		place-items: center;
		width: 44px;
		height: 44px;
		padding: 0;
		border-radius: var(--radius-pill);
		border: 1px solid var(--line2);
		background: color-mix(in srgb, var(--panel) 88%, transparent);
		color: var(--text);
	}
	.nav:hover,
	.close:hover {
		border-color: var(--gold-deep);
		color: var(--gold-copy);
	}
	.nav {
		grid-row: 1;
		justify-self: center;
	}
	.prev {
		grid-column: 1;
	}
	.next {
		grid-column: 3;
	}
	.close {
		position: absolute;
		top: 12px;
		right: 10px;
		z-index: 1;
	}
	.caption {
		grid-column: 1 / -1;
		grid-row: 2;
		margin: 10px 12px 0;
		text-align: center;
		color: var(--muted);
		font-size: 12.5px;
	}
	.caption b {
		color: var(--text);
		font-weight: 600;
	}
	@media (max-width: 720px) {
		.stage {
			grid-template-columns: 48px minmax(0, 1fr) 48px;
		}
		.nav {
			width: 38px;
			height: 38px;
		}
	}
</style>
