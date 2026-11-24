<script lang="ts">
	import { untrack } from 'svelte';
	import { flip } from 'svelte/animate';
	import {
		SHADOW_ITEM_MARKER_PROPERTY_NAME,
		dragHandleZone,
		type DndEvent
	} from 'svelte-dnd-action';
	import {
		bringBack,
		hate,
		moveDown,
		moveUp,
		rankingLine,
		showVariant,
		type Card,
		type Ranking,
		type Shown
	} from './panel';
	import ReviewCard from './ReviewCard.svelte';

	/** The rank panel: the designs in the owner's order, and the hate pile. Every move has a
	 *  button; dragging a card by its handle (pointer, touch, or keyboard) does the same. */
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
						{onUse}
						onShow={(id) => (shown = showVariant(shown, card, id))}
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
		{#each hatedItems as item (item.id)}
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
						hated
						{disabled}
						onUp={() => {}}
						onDown={() => {}}
						onHate={() => {}}
						onBringBack={() => apply(bringBack(ranking, card.key))}
						{onUse}
						onShow={(id) => (shown = showVariant(shown, card, id))}
					/>
				{/if}
			</div>
		{/each}
	</div>
	{#if hatedItems.length === 0}
		<p class="empty">Nothing hated. Press Hate on a design, or drag it here.</p>
	{/if}
</div>

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
