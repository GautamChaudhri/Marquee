<script lang="ts">
	import { untrack } from 'svelte';
	import { flip } from 'svelte/animate';
	import {
		SHADOW_ITEM_MARKER_PROPERTY_NAME,
		dragHandleZone,
		type DndEvent
	} from 'svelte-dnd-action';
	import DesignDialog from './DesignDialog.svelte';
	import Lightbox from './Lightbox.svelte';
	import {
		bringBack,
		closeups,
		hate,
		moveDown,
		moveUp,
		rankingLine,
		showVariant,
		shownVariant,
		type Card,
		type Ranking,
		type Shown
	} from './panel';
	import ReviewCard from './ReviewCard.svelte';

	/** The rank panel: the designs in the owner's order, and the hate pile. Every move has a
	 *  button; dragging a card by its handle (pointer, touch, or keyboard) does the same.
	 *
	 *  A card's poster opens its design in a popup, where a poster is chosen; a magnifier,
	 *  on a card or in the popup, shows one poster large. */
	let {
		cards,
		shown = $bindable(),
		ranking = $bindable(),
		disabled = false,
		onUse
	}: {
		cards: Card[];
		shown: Shown;
		ranking: Ranking;
		disabled?: boolean;
		onUse: (candidateId: number) => void;
	} = $props();

	type Item = { id: string; [SHADOW_ITEM_MARKER_PROPERTY_NAME]?: boolean };
	const FLIP_MS = 150;
	const toItems = (keys: string[]): Item[] => keys.map((id) => ({ id }));
	const keysOf = (items: Item[]) => items.map((item) => item.id);

	// The zones' own lists. They follow `ranking`, and a drag writes `ranking` when it ends.
	let orderItems = $state.raw<Item[]>(untrack(() => toItems(ranking.order)));
	let hatedItems = $state.raw<Item[]>(untrack(() => toItems(ranking.hated)));

	$effect(() => {
		const next = ranking;
		untrack(() => {
			if (keysOf(orderItems).join() !== next.order.join()) orderItems = toItems(next.order);
			if (keysOf(hatedItems).join() !== next.hated.join()) hatedItems = toItems(next.hated);
		});
	});

	const byKey = $derived(new Map(cards.map((card) => [card.key, card])));
	const line = $derived(rankingLine(ranking, cards));

	function apply(next: Ranking) {
		ranking = next;
	}

	// The design open in the popup, and the poster shown large, each by its card's key.
	let opened = $state<string | null>(null);
	let closeup = $state<{ key: string; id: number } | null>(null);

	/** Where a card stands: its place in the order, or in the hate pile, from 1. */
	function standing(key: string): { position: number; hated: boolean } {
		const hatedAt = ranking.hated.indexOf(key);
		return hatedAt >= 0
			? { position: hatedAt + 1, hated: true }
			: { position: ranking.order.indexOf(key) + 1, hated: false };
	}
	function titleOf(key: string): string {
		const { position, hated } = standing(key);
		return hated ? `Hated design ${position}` : `Design ${position}`;
	}

	const openedCard = $derived(opened === null ? undefined : byKey.get(opened));
	const closeupCard = $derived(closeup === null ? undefined : byKey.get(closeup.key));

	function zoom(card: Card, candidateId: number) {
		closeup = { key: card.key, id: candidateId };
	}

	function considerOrder(e: CustomEvent<DndEvent<Item>>) {
		orderItems = e.detail.items;
	}
	function considerHated(e: CustomEvent<DndEvent<Item>>) {
		hatedItems = e.detail.items;
	}
	function finalizeOrder(e: CustomEvent<DndEvent<Item>>) {
		orderItems = e.detail.items;
		ranking = { order: keysOf(orderItems), hated: keysOf(hatedItems) };
	}
	function finalizeHated(e: CustomEvent<DndEvent<Item>>) {
		hatedItems = e.detail.items;
		ranking = { order: keysOf(orderItems), hated: keysOf(hatedItems) };
	}

	const zone = (items: Item[]) => ({
		items,
		flipDurationMs: FLIP_MS,
		type: 'design',
		dragDisabled: disabled,
		dropTargetStyle: {},
		dropTargetClasses: ['drop-target'],
		// The handle sits in a corner of a tall card. Without this the card's middle decides
		// where it lands, well away from the pointer, and the hate pile is hard to hit.
		centreDraggedOnCursor: true,
		delayTouchStart: true
	});
</script>

<div class="panel">
	<p class="line" role="status" data-testid="ranking-line">{line}</p>

	<h3 id="order-heading">Your order <span class="hint">best first</span></h3>
	<div
		class="zone order"
		aria-labelledby="order-heading"
		data-testid="order-list"
		use:dragHandleZone={zone(orderItems)}
		onconsider={considerOrder}
		onfinalize={finalizeOrder}
	>
		{#each orderItems as item, i (item.id)}
			{@const card = byKey.get(item.id)}
			<div
				class="item"
				class:shadow={item[SHADOW_ITEM_MARKER_PROPERTY_NAME]}
				animate:flip={{ duration: FLIP_MS }}
			>
				{#if card}
					<ReviewCard
						{card}
						{shown}
						position={i + 1}
						first={i === 0}
						last={i === orderItems.length - 1}
						{disabled}
						onUp={() => apply(moveUp(ranking, card.key))}
						onDown={() => apply(moveDown(ranking, card.key))}
						onHate={() => apply(hate(ranking, card.key))}
						onBringBack={() => apply(bringBack(ranking, card.key))}
						onOpen={() => (opened = card.key)}
						onZoom={() => zoom(card, shownVariant(card, shown).id)}
					/>
				{/if}
			</div>
		{/each}
	</div>
	{#if orderItems.length === 0}
		<p class="empty">Every design is in the hate pile. Bring one back to save a ranking.</p>
	{/if}

	<h3 id="hate-heading">Hate pile <span class="hint">designs you do not want</span></h3>
	<div
		class="zone hated"
		aria-labelledby="hate-heading"
		data-testid="hate-pile"
		use:dragHandleZone={zone(hatedItems)}
		onconsider={considerHated}
		onfinalize={finalizeHated}
	>
		{#each hatedItems as item, i (item.id)}
			{@const card = byKey.get(item.id)}
			<div
				class="item"
				class:shadow={item[SHADOW_ITEM_MARKER_PROPERTY_NAME]}
				animate:flip={{ duration: FLIP_MS }}
			>
				{#if card}
					<ReviewCard
						{card}
						{shown}
						position={i + 1}
						hated
						{disabled}
						onUp={() => {}}
						onDown={() => {}}
						onHate={() => {}}
						onBringBack={() => apply(bringBack(ranking, card.key))}
						onOpen={() => (opened = card.key)}
						onZoom={() => zoom(card, shownVariant(card, shown).id)}
					/>
				{/if}
			</div>
		{/each}
	</div>
	{#if hatedItems.length === 0}
		<p class="empty">Nothing hated. Press Hate on a design, or drag it here.</p>
	{/if}
</div>

{#if openedCard}
	{@const card = openedCard}
	{@const at = standing(card.key)}
	<DesignDialog
		{card}
		{shown}
		position={at.position}
		hated={at.hated}
		{disabled}
		onSelect={(id) => (shown = showVariant(shown, card, id))}
		onUse={(id) => {
			opened = null;
			onUse(id);
		}}
		onHate={() => {
			// The card is read before the popup closes: closing it leaves no card to read.
			apply(hate(ranking, card.key));
			opened = null;
		}}
		onBringBack={() => {
			apply(bringBack(ranking, card.key));
			opened = null;
		}}
		onZoom={(id) => zoom(card, id)}
		onClose={() => (opened = null)}
	/>
{/if}

{#if closeup && closeupCard}
	<Lightbox
		items={closeups(closeupCard)}
		start={closeup.id}
		title={titleOf(closeupCard.key)}
		onClose={() => (closeup = null)}
	/>
{/if}

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.line {
		margin: 0;
		font-size: 13px;
		color: var(--muted);
	}
	h3 {
		margin: 8px 0 0;
		font-size: 14px;
	}
	.hint {
		margin-left: 6px;
		font-weight: 400;
		font-size: 12px;
		color: var(--faint);
	}
	.zone {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		/* Every card in a row is as tall as the row. */
		align-items: stretch;
		gap: 10px;
		min-height: 60px;
		padding: 6px;
		border-radius: var(--radius);
		border: 1px dashed transparent;
	}
	.zone.hated {
		border-color: var(--line2);
		background: color-mix(in srgb, var(--bad) 5%, transparent);
	}
	.zone:global(.drop-target) {
		border-color: var(--gold);
	}
	.item {
		min-width: 0;
	}
	.item.shadow {
		opacity: 0.35;
	}
	.empty {
		margin: -4px 0 0;
		font-size: 12.5px;
		color: var(--faint);
	}
	@media (max-width: 720px) {
		.zone {
			grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
			gap: 8px;
			padding: 4px;
		}
	}
</style>
