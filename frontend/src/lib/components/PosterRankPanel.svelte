<script lang="ts">
	import { dndzone } from 'svelte-dnd-action';
	import { flip } from 'svelte/animate';
	import { submitFeedback } from '$lib/api/feedback';
	import { toast } from '$lib/toast';
	import { gradientFor } from '$lib/display';
	import type { RankItem } from '$lib/api/types';

	let {
		runId,
		items,
		disabled = false,
		submitLabel = 'Submit ranking',
		onSubmit,
		onsubmitted,
		oncancel
	}: {
		runId?: string;
		items: RankItem[];
		disabled?: boolean;
		submitLabel?: string;
		/** Custom submit for an alternate evidence workflow. When given, it
		 *  replaces the default `submitFeedback` call; the caller owns the toast. */
		onSubmit?: (payload: { order: string[]; hated: string[] }) => Promise<void>;
		onsubmitted?: (eventId: string) => void;
		oncancel?: () => void;
	} = $props();

	type DndItem = RankItem & { id: string };
	const toDndItem = (item: RankItem): DndItem => ({ ...item, id: item.key });

	const FLIP_MS = 150;
	// svelte-ignore state_referenced_locally
	const seededOrder = items.map((i) => i.key);

	// `orderable` starts seeded in the pipeline's own predicted order (the
	// baseline the backend compares against — see design/30). Dragging only
	// matters where the final order disagrees with that baseline; leaving a
	// card where it started carries no training signal. Throwing a card into
	// the hate pile removes it from the orderable list entirely.
	// svelte-ignore state_referenced_locally
	let orderable = $state<DndItem[]>(items.map(toDndItem));
	let hatePile = $state<DndItem[]>([]);
	let busy = $state(false);

	const reordered = $derived(
		orderable.length !== seededOrder.length ||
			orderable.some((item, i) => item.key !== seededOrder[i])
	);
	const dirty = $derived(hatePile.length > 0 || reordered);

	function handleOrderableDnd(e: CustomEvent<{ items: DndItem[] }>) {
		orderable = e.detail.items;
	}
	function handleHateDnd(e: CustomEvent<{ items: DndItem[] }>) {
		hatePile = e.detail.items;
	}

	function hateItem(key: string) {
		const idx = orderable.findIndex((i) => i.key === key);
		if (idx === -1) return;
		const [item] = orderable.splice(idx, 1);
		hatePile.push(item);
	}
	function restoreItem(key: string) {
		const idx = hatePile.findIndex((i) => i.key === key);
		if (idx === -1) return;
		const [item] = hatePile.splice(idx, 1);
		orderable.push(item);
	}

	function buildPayload(): { order: string[]; hated: string[] } {
		return {
			order: orderable.flatMap((i) => i.filenames),
			hated: hatePile.flatMap((i) => i.filenames)
		};
	}

	async function submit() {
		const { order, hated } = buildPayload();
		if (!dirty) {
			toast('Drag to reorder or hate a poster first', 'info');
			return;
		}
		busy = true;
		try {
			if (onSubmit) {
				await onSubmit({ order, hated });
				onsubmitted?.('');
			} else {
				const res = await submitFeedback(fetch, {
					run_id: runId ?? '',
					action: 'rank',
					order,
					hated
				});
				toast('Ranking saved — training the Key Art Engine', 'good');
				onsubmitted?.(res.event_id);
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Ranking failed', 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<div class="panel">
	<div class="bar">
		<div class="intro">
			<strong>Rank by taste.</strong> Drag posters into your preferred order — only the cards you
			move teach the Key Art Engine anything; the rest stay as the pipeline ranked them. Drag (or
			send) a poster you dislike into <em>Hate</em>. Whole design stacks move together.
		</div>
		<div class="counts">
			<span class="chip fav">{orderable.length} ranked</span>
			<span class="chip hate">{hatePile.length} hated</span>
		</div>
		<div class="bar-actions">
			<button class="btn-ghost" onclick={() => oncancel?.()} disabled={busy}>Cancel</button>
			<button class="btn-gold" onclick={submit} disabled={busy || disabled}>
				{busy ? 'Saving…' : submitLabel}
			</button>
		</div>
	</div>

	<div
		class="grid"
		aria-label="Ranked posters, best to worst"
		use:dndzone={{ items: orderable, flipDurationMs: FLIP_MS, dragDisabled: disabled }}
		onconsider={handleOrderableDnd}
		onfinalize={handleOrderableDnd}
	>
		{#each orderable as item, i (item.id)}
			{@const g = gradientFor(item.filenames[0] ?? item.key)}
			<div class="cell" animate:flip={{ duration: FLIP_MS }}>
				<div class="card" aria-label={item.label || item.key} style="--c0:{g[0]}; --c1:{g[1]}">
					<div class="art">
						<!-- On failure the card's gradient stands in for the image. -->
						<img
							src={item.posterUrl}
							alt={item.label}
							draggable="false"
							loading="lazy"
							decoding="async"
							onerror={(e) => ((e.currentTarget as HTMLImageElement).hidden = true)}
						/>
						<span class="pos mono">{i + 1}</span>
						{#if item.filenames.length > 1}
							<span class="badge mono">{item.filenames.length}</span>
						{/if}
					</div>
					<div class="cap">
						<span class="label mono">{item.label}</span>
						<button
							class="hate-btn"
							title="Send to hate pile"
							onclick={() => hateItem(item.key)}
							{disabled}>✕</button
						>
					</div>
				</div>
			</div>
		{/each}
	</div>

	<div class="hate-section">
		<div class="hate-head">
			<span class="hate-title">Hate pile</span>
			<span class="hate-hint">Posters here are excluded and weigh against similar designs.</span>
		</div>
		<div
			class="hate-zone"
			class:empty={hatePile.length === 0}
			aria-label="Hated posters"
			use:dndzone={{ items: hatePile, flipDurationMs: FLIP_MS, dragDisabled: disabled }}
			onconsider={handleHateDnd}
			onfinalize={handleHateDnd}
		>
			{#each hatePile as item (item.id)}
				{@const g = gradientFor(item.filenames[0] ?? item.key)}
				<div class="cell small" animate:flip={{ duration: FLIP_MS }}>
					<div
						class="card hated"
						aria-label={item.label || item.key}
						style="--c0:{g[0]}; --c1:{g[1]}"
					>
						<div class="art">
							<img
								src={item.posterUrl}
								alt={item.label}
								draggable="false"
								loading="lazy"
								decoding="async"
								onerror={(e) => ((e.currentTarget as HTMLImageElement).hidden = true)}
							/>
							{#if item.filenames.length > 1}
								<span class="badge mono">{item.filenames.length}</span>
							{/if}
						</div>
						<div class="cap">
							<span class="label mono">{item.label}</span>
							<button
								class="restore-btn"
								title="Restore to ranking"
								onclick={() => restoreItem(item.key)}
								{disabled}>↺</button
							>
						</div>
					</div>
				</div>
			{:else}
				{#if hatePile.length === 0}
					<span class="hate-empty">Drag a poster here, or use its ✕ button.</span>
				{/if}
			{/each}
		</div>
	</div>
</div>

<style>
	.panel {
		margin: 8px 0 20px;
	}
	.bar {
		display: flex;
		align-items: center;
		gap: 14px;
		flex-wrap: wrap;
		padding: 12px 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--gold-soft);
		margin-bottom: 14px;
	}
	.intro {
		flex: 1 1 320px;
		font-size: 13px;
		color: var(--muted);
		line-height: 1.45;
	}
	.intro strong {
		color: var(--text);
	}
	.counts {
		display: flex;
		gap: 8px;
	}
	.chip {
		font-size: 12px;
		padding: 3px 9px;
		border-radius: 11px;
		border: 1px solid var(--line2);
		color: var(--muted);
	}
	.chip.fav {
		border-color: var(--gold);
		color: var(--gold);
	}
	.chip.hate {
		border-color: var(--bad);
		color: var(--bad);
	}
	.bar-actions {
		display: flex;
		gap: 8px;
		margin-left: auto;
	}

	.grid {
		display: flex;
		flex-wrap: wrap;
		gap: 14px;
		min-height: 96px;
	}
	.cell {
		width: 120px;
	}
	.cell.small {
		width: 96px;
	}

	.card {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 6px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		cursor: grab;
		user-select: none;
	}
	.card:active {
		cursor: grabbing;
	}
	.card.hated {
		opacity: 0.82;
	}
	.art {
		position: relative;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: linear-gradient(165deg, var(--c0), var(--c1));
	}
	.art img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		pointer-events: none;
	}
	.pos {
		position: absolute;
		top: 4px;
		left: 4px;
		min-width: 20px;
		height: 20px;
		padding: 0 5px;
		border-radius: 10px;
		background: var(--gold);
		color: var(--on-gold);
		font-size: 12px;
		font-weight: 700;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.badge {
		position: absolute;
		top: 4px;
		right: 4px;
		min-width: 18px;
		height: 18px;
		padding: 0 5px;
		border-radius: 9px;
		background: color-mix(in srgb, var(--ink) 72%, transparent);
		color: var(--text);
		font-size: 11px;
		display: flex;
		align-items: center;
		justify-content: center;
	}
	.cap {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.label {
		flex: 1;
		min-width: 0;
		font-size: 11.5px;
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.hate-btn,
	.restore-btn {
		flex-shrink: 0;
		width: 20px;
		height: 20px;
		padding: 0;
		border: 1px solid var(--line2);
		border-radius: 6px;
		background: var(--panel2);
		color: var(--muted);
		font-size: 11px;
		line-height: 1;
		cursor: pointer;
	}
	.hate-btn:hover:not(:disabled) {
		color: var(--bad);
		border-color: var(--bad);
	}
	.restore-btn:hover:not(:disabled) {
		color: var(--gold);
		border-color: var(--gold);
	}
	.hate-btn:disabled,
	.restore-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.hate-section {
		margin-top: 18px;
		padding-top: 14px;
		border-top: 1px dashed var(--line2);
	}
	.hate-head {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 8px;
	}
	.hate-title {
		font-size: 12px;
		font-weight: 600;
		color: var(--bad);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.hate-hint {
		font-size: 12px;
		color: var(--muted);
	}
	.hate-zone {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 12px;
		min-height: 76px;
		padding: 10px;
		border: 1px dashed color-mix(in srgb, var(--bad) 45%, var(--line2));
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--bad) 6%, var(--panel));
	}
	.hate-zone.empty {
		align-items: center;
		justify-content: center;
	}
	.hate-empty {
		font-size: 12px;
		color: var(--muted);
	}
	.mono {
		font-family: var(--font-mono);
	}

	/* Buttons (defined locally — the app's .btn-* are page-scoped, not global) */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.btn-gold:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	.btn-ghost {
		padding: 8px 14px;
		border-radius: 8px;
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-size: 13px;
	}
	.btn-ghost:hover:not(:disabled) {
		color: var(--text);
		background: var(--panel2);
	}
	.btn-ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
