<script lang="ts">
	import { dragHandle } from 'svelte-dnd-action';
	import { formatScore } from '#lib/format.ts';
	import CountDot from '#lib/ui/CountDot.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import { answerable, shownVariant, type Card, type Shown } from './panel';
	import ScoreBar from './ScoreBar.svelte';

	/** One design in the owner's order or in the hate pile. Every card has the same four rows,
	 *  so every card is the same height: the poster, the design's place and score, what the
	 *  score is made of, and the buttons that move, hate, or bring it back.
	 *
	 *  The poster opens the design (its variants, its score, and the button that chooses it);
	 *  the magnifier shows the poster large. Dragging uses the handle; every move also has a
	 *  button. */
	let {
		card,
		shown,
		position,
		hated = false,
		first = false,
		last = false,
		disabled = false,
		onUp,
		onDown,
		onHate,
		onBringBack,
		onOpen,
		onZoom
	}: {
		card: Card;
		shown: Shown;
		/** Its place in the owner's order, or in the hate pile, from 1. */
		position: number;
		hated?: boolean;
		first?: boolean;
		last?: boolean;
		disabled?: boolean;
		onUp: () => void;
		onDown: () => void;
		onHate: () => void;
		onBringBack: () => void;
		onOpen: () => void;
		onZoom: () => void;
	} = $props();

	const variant = $derived(shownVariant(card, shown));
	const ok = $derived(answerable(variant));
	const name = $derived(hated ? `hated design ${position}` : `design ${position}`);
	const never = 'Never downloaded: it cannot be chosen or hated.';
</script>

<article
	class="card"
	class:pick={variant.is_pick}
	class:hated
	aria-label={hated ? `Hated design ${position}` : `Design ${position}`}
	data-testid="review-card"
	data-key={card.key}
	data-candidate={variant.id}
>
	<div class="frame">
		<button
			class="open"
			onclick={onOpen}
			aria-label="Open {name}"
			aria-haspopup="dialog"
			title={card.variants.length > 1 ? 'See its variants and choose one' : 'See it and choose it'}
			data-testid="open-design"
		>
			{#if variant.image_url}
				<img src={variant.image_url} alt="" width="500" height="750" loading="lazy" />
			{:else}
				<span class="none">Never downloaded</span>
			{/if}
			{#if card.variants.length > 1}
				<CountDot count={card.variants.length} word="variants" testid="variant-count" />
			{/if}
		</button>
		{#if variant.is_pick}<span class="mark">Pick</span>{/if}
		<!-- A span, not a button: the drag library will not start a drag from an element that
		     has a value, which every button does. The action gives it its role and tab stop. -->
		<span
			class="corner grip"
			class:off={disabled}
			role="button"
			tabindex="0"
			use:dragHandle
			aria-label="Drag {name}"
			title="Drag to reorder, or to the other list">⠿</span
		>
		<button
			class="corner zoom"
			onclick={onZoom}
			disabled={!variant.image_url}
			aria-label="Look closer at {name}"
			title="Look closer"
			data-testid="zoom"
		>
			<Icon name="zoom-in" size={15} />
		</button>
	</div>
	<div class="facts">
		{#if hated}
			<span class="rank faded">Hated</span>
		{:else}
			<span class="rank" title="Its place in your order" data-testid="design-rank">#{position}</span
			>
		{/if}
		<span class="score" title="Final score">{formatScore(variant.final_score)}</span>
	</div>
	<ScoreBar parts={variant.score_parts} />
	<div class="buttons">
		{#if hated}
			<button class="wide" onclick={onBringBack} {disabled} aria-label="Bring back {name}"
				>Bring back</button
			>
		{:else}
			<button
				class="arrow"
				onclick={onUp}
				disabled={disabled || first}
				aria-label="Move {name} up"
				title="Move up">↑</button
			>
			<button
				class="arrow"
				onclick={onDown}
				disabled={disabled || last}
				aria-label="Move {name} down"
				title="Move down">↓</button
			>
			<button
				class="hate wide"
				onclick={onHate}
				disabled={disabled || !ok}
				aria-label="Hate {name}"
				title={ok ? undefined : never}>Hate</button
			>
		{/if}
	</div>
</article>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 7px;
		height: 100%;
		min-width: 0;
		padding: 6px;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel);
	}
	.card.pick {
		border-color: var(--gold-deep);
	}
	.card.hated {
		opacity: 0.85;
		border-style: dashed;
	}
	.frame {
		position: relative;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel2);
	}
	.open {
		position: relative;
		display: block;
		width: 100%;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		cursor: pointer;
	}
	.open:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: -2px;
	}
	.open img,
	.none {
		display: block;
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
	}
	.none {
		display: grid;
		place-items: center;
		padding: 8px;
		color: var(--faint);
		font-size: 12px;
		text-align: center;
	}
	.mark {
		position: absolute;
		top: 6px;
		left: 6px;
		padding: 2px 8px;
		border-radius: var(--radius-pill);
		background: var(--gold);
		color: var(--on-gold);
		font-size: 11px;
		font-weight: 700;
		pointer-events: none;
	}
	.corner {
		position: absolute;
		bottom: 6px;
		width: 30px;
		height: 30px;
		padding: 0;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: color-mix(in srgb, var(--panel) 88%, transparent);
		color: var(--text);
		display: grid;
		place-items: center;
	}
	.grip {
		left: 6px;
		color: var(--muted);
		cursor: grab;
		font-size: 15px;
		line-height: 1;
		user-select: none;
		touch-action: none;
	}
	.grip.off {
		opacity: 0.45;
	}
	.zoom {
		right: 6px;
		cursor: zoom-in;
	}
	.zoom:hover:not(:disabled) {
		border-color: var(--gold-deep);
		color: var(--gold-copy);
	}
	.facts {
		display: flex;
		align-items: baseline;
		gap: 8px;
		font-variant-numeric: tabular-nums;
	}
	.rank {
		font-size: 15px;
		font-weight: 650;
		line-height: 1.2;
	}
	.rank.faded {
		font-size: 12.5px;
		line-height: 18px;
		color: var(--muted);
	}
	.score {
		color: var(--muted);
		font-size: 12px;
	}
	.buttons {
		display: flex;
		gap: 4px;
		margin-top: auto;
	}
	.buttons button {
		min-height: 30px;
		padding: 0 8px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 12px;
	}
	.buttons .arrow {
		flex: none;
		width: 30px;
		padding: 0;
	}
	.buttons .wide {
		flex: 1 1 auto;
		min-width: 0;
	}
	.buttons .hate {
		color: var(--bad);
	}
	.buttons button:disabled,
	.zoom:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
</style>
