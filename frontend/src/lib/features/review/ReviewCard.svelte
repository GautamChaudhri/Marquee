<script lang="ts">
	import { dragHandle } from 'svelte-dnd-action';
	import { formatScore } from '#lib/format.ts';
	import { answerable, shownVariant, type Card, type Shown } from './panel';

	/** One design: the variant it shows, its rank, score, and stack, and the buttons that move,
	 *  hate, bring back, or choose it. Dragging uses the handle; every move also has a button. */
	let {
		card,
		shown,
		position = null,
		hated = false,
		first = false,
		last = false,
		disabled = false,
		onUp,
		onDown,
		onHate,
		onBringBack,
		onUse,
		onShow
	}: {
		card: Card;
		shown: Shown;
		/** Its place in the owner's order, from 1; null in the hate pile. */
		position?: number | null;
		hated?: boolean;
		first?: boolean;
		last?: boolean;
		disabled?: boolean;
		onUp: () => void;
		onDown: () => void;
		onHate: () => void;
		onBringBack: () => void;
		onUse: (candidateId: number) => void;
		onShow: (candidateId: number) => void;
	} = $props();

	const variant = $derived(shownVariant(card, shown));
	const ok = $derived(answerable(variant));
	const name = $derived(`design ranked ${variant.rank}`);
	let open = $state(false);
</script>

<article
	class="card"
	class:pick={variant.is_pick}
	class:hated
	aria-label="{hated ? 'Hated ' : ''}{name}"
	data-testid="review-card"
	data-key={card.key}
	data-candidate={variant.id}
>
	<div class="frame">
		{#if variant.image_url}
			<img
				src={variant.image_url}
				alt="Candidate ranked {variant.rank}"
				width="500"
				height="750"
				loading="lazy"
			/>
		{:else}
			<div class="none">No image</div>
		{/if}
		{#if position !== null}<span class="position" title="Your order">{position}</span>{/if}
		{#if variant.is_pick}<span class="mark">Pick</span>{/if}
		<!-- A span, not a button: the drag library will not start a drag from an element that
		     has a value, which every button does. The action gives it its role and tab stop. -->
		<span
			class="grip"
			class:off={disabled}
			role="button"
			tabindex="0"
			use:dragHandle
			aria-label="Drag {name}"
			title="Drag to reorder, or to the other list">⠿</span
		>
	</div>
	<div class="facts">
		<span class="rank">#{variant.rank}</span>
		<span class="score" title="Final score">{formatScore(variant.final_score)}</span>
		{#if variant.stack}<span class="stack" title="Design stack">{variant.stack}</span>{/if}
	</div>
	{#if card.variants.length > 1}
		<button
			class="variants-toggle"
			aria-expanded={open}
			onclick={() => (open = !open)}
			data-testid="variants-toggle"
		>
			{card.variants.length} variants {open ? '▴' : '▾'}
		</button>
		{#if open}
			<ul class="variants" aria-label="Variants of stack {card.stack}">
				{#each card.variants as other (other.id)}
					<li>
						<button
							class="variant"
							class:on={other.id === variant.id}
							aria-pressed={other.id === variant.id}
							aria-label="Show variant {other.stack ?? other.id} (rank {other.rank})"
							onclick={() => onShow(other.id)}
							{disabled}
						>
							{#if other.image_url}
								<img src={other.image_url} alt="" width="500" height="750" loading="lazy" />
							{/if}
							<span>{other.stack ?? '—'} · #{other.rank}</span>
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
	{#if !ok}
		<p class="warn">Never downloaded: it cannot be chosen or hated.</p>
	{/if}
	<div class="buttons">
		{#if hated}
			<button onclick={onBringBack} {disabled} aria-label="Bring back {name}">Bring back</button>
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
			<button class="hate" onclick={onHate} disabled={disabled || !ok} aria-label="Hate {name}"
				>Hate</button
			>
			<button
				class="use"
				onclick={() => onUse(variant.id)}
				disabled={disabled || !ok}
				aria-label="Use {name}">Use this one</button
			>
		{/if}
	</div>
</article>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 6px;
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
	.frame img {
		display: block;
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
	}
	.none {
		aspect-ratio: 2 / 3;
		display: grid;
		place-items: center;
		color: var(--faint);
		font-size: 12px;
	}
	.position,
	.mark {
		position: absolute;
		top: 6px;
		padding: 2px 7px;
		border-radius: var(--radius-pill);
		font-size: 11px;
		font-weight: 700;
	}
	.position {
		left: 6px;
		background: var(--ink);
		color: var(--text);
		border: 1px solid var(--line2);
		font-variant-numeric: tabular-nums;
	}
	.mark {
		right: 6px;
		background: var(--gold);
		color: var(--on-gold);
	}
	.grip {
		position: absolute;
		bottom: 6px;
		right: 6px;
		width: 30px;
		height: 30px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--muted);
		cursor: grab;
		font-size: 15px;
		line-height: 1;
		display: grid;
		place-items: center;
		user-select: none;
		touch-action: none;
	}
	.grip.off {
		opacity: 0.45;
	}
	.facts {
		display: flex;
		gap: 8px;
		font-size: 12px;
		font-variant-numeric: tabular-nums;
	}
	.rank {
		font-weight: 650;
	}
	.score {
		color: var(--muted);
	}
	.stack {
		margin-left: auto;
		color: var(--faint);
		font-family: var(--font-mono);
		font-size: 11px;
	}
	.variants-toggle {
		align-self: flex-start;
		border: 0;
		background: none;
		padding: 0;
		color: var(--gold-copy);
		font-size: 12px;
	}
	.variants {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 4px;
	}
	.variant {
		width: 100%;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 2px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel2);
		color: var(--muted);
		font-size: 10.5px;
	}
	.variant.on {
		border-color: var(--gold);
		color: var(--text);
	}
	.variant img {
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: 4px;
	}
	.warn {
		margin: 0;
		font-size: 11.5px;
		color: var(--warn-copy);
	}
	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
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
		width: 30px;
		padding: 0;
	}
	.buttons .hate {
		color: var(--bad);
	}
	.buttons .use {
		flex: 1 0 auto;
		border-color: var(--gold-deep);
		color: var(--gold-copy);
	}
	.buttons button:disabled,
	.variant:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
</style>
