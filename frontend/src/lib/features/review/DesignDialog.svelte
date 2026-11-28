<script lang="ts">
	import { formatScore } from '#lib/format.ts';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import Modal from '#lib/ui/Modal.svelte';
	import { answerable, shownVariant, type Card, type Shown } from './panel';
	import ScoreBar from './ScoreBar.svelte';
	import ScoreBreakdown from './ScoreBreakdown.svelte';

	/** One design, opened. On the left the selected poster, larger, with its file's size under
	 *  it and its score laid out part by part; on the right a gallery of the design's variants,
	 *  numbered within the design. A design with more variants than fit scrolls its gallery;
	 *  the rest of the popup stays put. A lone poster has no gallery: its score sits beside it.
	 *  This is the one place a poster is chosen, for a design with many variants and for a lone
	 *  poster alike: select, then "Use this one".
	 *
	 *  Selecting a variant also makes it the one the card shows, so a saved ranking names it. */
	let {
		card,
		shown,
		position,
		hated = false,
		disabled = false,
		onSelect,
		onUse,
		onHate,
		onBringBack,
		onZoom,
		onClose
	}: {
		card: Card;
		shown: Shown;
		/** The design's place in the owner's order, or in the hate pile, from 1. */
		position: number;
		hated?: boolean;
		disabled?: boolean;
		onSelect: (candidateId: number) => void;
		onUse: (candidateId: number) => void;
		onHate: () => void;
		onBringBack: () => void;
		onZoom: (candidateId: number) => void;
		onClose: () => void;
	} = $props();

	const selected = $derived(shownVariant(card, shown));
	const place = $derived(card.variants.findIndex((v) => v.id === selected.id) + 1);
	const many = $derived(card.variants.length > 1);
	const ok = $derived(answerable(selected));
	const title = $derived(hated ? `Hated design ${position}` : `Design ${position}`);
	// The larger view shows the run's large copy when it kept one, else the 500 px file.
	const picture = $derived(selected.large_image_url ?? selected.image_url);

	// The size of the file in the larger view, known once it has loaded.
	let loaded = $state<{ id: number; width: number; height: number } | null>(null);
	const resolution = $derived(
		loaded?.id === selected.id ? `${loaded.width} × ${loaded.height} px` : null
	);

	function measure(id: number, e: Event) {
		const image = e.currentTarget as HTMLImageElement;
		if (image.naturalWidth > 0) {
			loaded = { id, width: image.naturalWidth, height: image.naturalHeight };
		}
	}
</script>

<Modal label={title} {onClose} maxWidth={many ? '1040px' : '760px'} testid="design-dialog">
	<header class="head">
		<h2>{title}</h2>
		<span class="sub">{many ? `${card.variants.length} variants, best first` : '1 poster'}</span>
		<button class="close" onclick={onClose} aria-label="Close"><Icon name="x" size={18} /></button>
	</header>

	<div class="body" class:many>
		<section
			class="detail"
			class:many
			aria-label="The selected poster and its score"
			data-testid="design-detail"
		>
			<div class="hero">
				<div class="frame">
					{#if picture}
						{#key selected.id}
							<img
								class="poster"
								src={picture}
								alt="{title}{many ? `, variant ${place}` : ''}"
								data-testid="selected-poster"
								data-candidate={selected.id}
								onload={(e) => measure(selected.id, e)}
							/>
						{/key}
					{:else}
						<span class="poster none">Never downloaded</span>
					{/if}
					<button
						class="zoom"
						onclick={() => onZoom(selected.id)}
						disabled={!picture}
						aria-label="Look closer at the selected poster"
						title="Look closer"
						data-testid="zoom"
					>
						<Icon name="zoom-in" size={16} />
					</button>
				</div>
				<p class="resolution" data-testid="poster-resolution">{resolution ?? '\u00a0'}</p>
			</div>
			<div class="scores" data-testid="design-scores">
				<div>
					<div class="big">{formatScore(selected.final_score)}</div>
					<div class="sub">
						{many ? `Variant ${place} of ${card.variants.length}` : 'Final score'}{selected.is_pick
							? ' · the run’s top pick'
							: ''}
					</div>
				</div>
				<ScoreBar parts={selected.score_parts} large />
				<ScoreBreakdown parts={selected.score_parts} />
			</div>
		</section>

		{#if many}
			<ul class="gallery" aria-label="Variants of this design" data-testid="variants">
				{#each card.variants as variant, i (variant.id)}
					{@const on = variant.id === selected.id}
					<li class="tile" class:on data-testid="variant" data-candidate={variant.id}>
						<div class="frame">
							<button
								class="choose"
								aria-pressed={on}
								aria-label="Select variant {i + 1}"
								onclick={() => onSelect(variant.id)}
								{disabled}
							>
								{#if variant.image_url}
									<img
										class="poster"
										src={variant.image_url}
										alt=""
										width="500"
										height="750"
										loading="lazy"
									/>
								{:else}
									<span class="poster none">Never downloaded</span>
								{/if}
							</button>
							{#if on}<span class="tick"><Icon name="check" size={13} stroke={3} /></span>{/if}
							<button
								class="zoom"
								onclick={() => onZoom(variant.id)}
								disabled={!variant.image_url}
								aria-label="Look closer at variant {i + 1}"
								title="Look closer"
							>
								<Icon name="zoom-in" size={15} />
							</button>
						</div>
						<div class="facts">
							<span class="rank" title="Its place among this design’s variants">#{i + 1}</span>
							<span class="score">{formatScore(variant.final_score)}</span>
						</div>
						<ScoreBar parts={variant.score_parts} />
					</li>
				{/each}
			</ul>
		{/if}
	</div>

	<footer class="foot">
		<p class="say" role="status" data-testid="design-selection">
			{#if !ok}
				Never downloaded: it cannot be chosen or hated.
			{:else if many}
				Variant {place} of {card.variants.length} selected
			{:else}
				The only poster of this design
			{/if}
		</p>
		{#if hated}
			<Button onclick={onBringBack} {disabled}>Bring back</Button>
		{:else}
			<Button variant="danger" onclick={onHate} disabled={disabled || !ok}>Hate this design</Button>
		{/if}
		<Button
			variant="primary"
			onclick={() => onUse(selected.id)}
			disabled={disabled || !ok}
			data-testid="use-this-one">Use this one</Button
		>
	</footer>
</Modal>

<style>
	.head {
		display: flex;
		align-items: baseline;
		gap: 10px;
		padding: 14px 18px;
		border-bottom: 1px solid var(--line);
	}
	h2 {
		margin: 0;
		font-size: 15px;
	}
	.sub {
		color: var(--muted);
		font-size: 12.5px;
	}
	.close {
		margin-left: auto;
		align-self: center;
		display: grid;
		place-items: center;
		width: 30px;
		height: 30px;
		padding: 0;
		border: 0;
		border-radius: var(--radius-sm);
		background: none;
		color: var(--muted);
	}
	.close:hover {
		background: var(--panel2);
		color: var(--text);
	}
	/* The body is as tall as the popup leaves it and no taller; what does not fit scrolls
	   inside the gallery (or, for a long breakdown, inside the left column). */
	.body {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		grid-template-rows: minmax(0, 1fr);
		gap: 20px;
		padding: 15px;
		min-height: 0;
		overflow: hidden;
	}
	.body.many {
		grid-template-columns: 280px minmax(0, 1fr);
	}
	/* A lone poster: the picture, and its score beside it. With variants: one column, the
	   picture over its score. The picture is the same size either way. */
	.detail {
		display: flex;
		align-items: flex-start;
		gap: 20px;
		min-width: 0;
		min-height: 0;
		padding: 3px;
		overflow-y: auto;
		overscroll-behavior: contain;
	}
	.detail.many {
		flex-direction: column;
		align-items: stretch;
		gap: 12px;
	}
	.hero {
		flex: none;
		width: 274px;
		max-width: 100%;
	}
	.scores {
		flex: 1 1 0;
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-width: 0;
	}
	.detail.many .scores {
		flex: none;
	}
	.resolution {
		margin: 6px 0 0;
		color: var(--faint);
		font-size: 12px;
		font-variant-numeric: tabular-nums;
	}
	.gallery {
		list-style: none;
		margin: 0;
		/* Room for the selected poster's ring, which the scroll box would cut off. */
		padding: 3px;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(124px, 1fr));
		gap: 14px 12px;
		align-content: start;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
	}
	.tile {
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}
	.frame {
		position: relative;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel2);
		outline: 2px solid transparent;
		outline-offset: 1px;
	}
	.tile.on .frame {
		outline-color: var(--gold);
	}
	.choose {
		display: block;
		width: 100%;
		padding: 0;
		border: 0;
		background: none;
		color: inherit;
		cursor: pointer;
	}
	.choose:focus-visible {
		outline: 2px solid var(--text);
		outline-offset: -2px;
	}
	.poster {
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
	.tick {
		position: absolute;
		top: 6px;
		right: 6px;
		width: 22px;
		height: 22px;
		display: grid;
		place-items: center;
		border-radius: var(--radius-pill);
		background: var(--gold);
		color: var(--on-gold);
		pointer-events: none;
	}
	.zoom {
		position: absolute;
		right: 6px;
		bottom: 6px;
		width: 30px;
		height: 30px;
		padding: 0;
		display: grid;
		place-items: center;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: color-mix(in srgb, var(--panel) 88%, transparent);
		color: var(--text);
		cursor: zoom-in;
	}
	.zoom:hover:not(:disabled) {
		border-color: var(--gold-deep);
		color: var(--gold-copy);
	}
	.zoom:disabled,
	.choose:disabled {
		opacity: 0.45;
		cursor: not-allowed;
	}
	/* With a mouse a magnifier shows only while the pointer is on its poster, or while it has
	   the keyboard's focus. A touch screen has no hover, so there it stays. */
	@media (hover: hover) {
		.zoom {
			opacity: 0;
			transition: opacity 0.18s ease;
		}
		.frame:hover .zoom,
		.zoom:focus-visible {
			opacity: 1;
		}
		.frame:hover .zoom:disabled {
			opacity: 0.45;
		}
	}
	.facts {
		display: flex;
		align-items: baseline;
		gap: 8px;
		font-variant-numeric: tabular-nums;
	}
	.rank {
		font-size: 14px;
		font-weight: 650;
	}
	.score {
		color: var(--muted);
		font-size: 12px;
	}
	.big {
		font-size: 26px;
		font-weight: 700;
		line-height: 1.1;
		font-variant-numeric: tabular-nums;
	}
	.foot {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		padding: 12px 18px;
		border-top: 1px solid var(--line);
	}
	.say {
		margin: 0 auto 0 0;
		color: var(--muted);
		font-size: 12.5px;
	}
	/* On a phone the whole body scrolls as one: the picture, the variants, then the score. */
	@media (max-width: 760px) {
		.body,
		.body.many {
			display: flex;
			flex-direction: column;
			padding: 11px;
			overflow-y: auto;
			overscroll-behavior: contain;
		}
		.detail,
		.detail.many {
			display: contents;
		}
		.hero {
			order: 1;
			width: 200px;
			align-self: center;
		}
		.gallery {
			order: 2;
			flex: none;
			overflow: visible;
			grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
		}
		.scores,
		.detail.many .scores {
			order: 3;
			flex: none;
		}
	}
</style>
