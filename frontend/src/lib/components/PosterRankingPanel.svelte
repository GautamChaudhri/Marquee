<script lang="ts">
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
		/** Custom submit (e.g. onboarding's taste-test endpoint). When given, it
		 *  replaces the default `submitFeedback` call; the caller owns the toast. */
		onSubmit?: (payload: { favorites: string[][]; hated: string[] }) => Promise<void>;
		onsubmitted?: (eventId: string) => void;
		oncancel?: () => void;
	} = $props();

	// A bucket is either a favorite tier (1-based number), 'hate', or 'neutral'.
	type Bucket = number | 'hate' | 'neutral';
	let assign = $state<Record<string, Bucket>>({});
	let busy = $state(false);

	function bucketOf(key: string): Bucket {
		return assign[key] ?? 'neutral';
	}
	function setFav(key: string) {
		if (typeof assign[key] !== 'number') assign[key] = 1;
	}
	function bumpTier(key: string, delta: number) {
		const current = typeof assign[key] === 'number' ? (assign[key] as number) : 1;
		assign[key] = Math.max(1, current + delta);
	}

	const favCount = $derived(items.filter((i) => typeof bucketOf(i.key) === 'number').length);
	const hateCount = $derived(items.filter((i) => bucketOf(i.key) === 'hate').length);

	/** Group favorites by tier, sort tiers ascending → [[tier1…],[tier2…]]. */
	function buildPayload(): { favorites: string[][]; hated: string[] } {
		const tiers: Record<number, string[]> = {};
		const hated: string[] = [];
		for (const item of items) {
			const bucket = bucketOf(item.key);
			if (typeof bucket === 'number') {
				(tiers[bucket] ??= []).push(...item.filenames);
			} else if (bucket === 'hate') {
				hated.push(...item.filenames);
			}
		}
		const favorites = Object.keys(tiers)
			.map(Number)
			.sort((a, b) => a - b)
			.map((t) => tiers[t]);
		return { favorites, hated };
	}

	async function submit() {
		const { favorites, hated } = buildPayload();
		if (!favorites.length && !hated.length) {
			toast('Mark at least one favorite or hated poster first', 'info');
			return;
		}
		busy = true;
		try {
			if (onSubmit) {
				await onSubmit({ favorites, hated });
				onsubmitted?.('');
			} else {
				const res = await submitFeedback(fetch, {
					run_id: runId ?? '',
					action: 'rank',
					favorites,
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
			<strong>Rank by taste.</strong> Mark your favorites (set a tier — 1 is best, ties allowed) and
			drop the ones you dislike into <em>Hate</em>. Everything left is treated as neutral. Whole
			design stacks move together.
		</div>
		<div class="counts">
			<span class="chip fav">{favCount} favorite{favCount === 1 ? '' : 's'}</span>
			<span class="chip hate">{hateCount} hated</span>
		</div>
		<div class="bar-actions">
			<button class="btn-ghost" onclick={() => oncancel?.()} disabled={busy}>Cancel</button>
			<button class="btn-gold" onclick={submit} disabled={busy || disabled}>
				{busy ? 'Saving…' : submitLabel}
			</button>
		</div>
	</div>

	<div class="grid">
		{#each items as item (item.key)}
			{@const bucket = bucketOf(item.key)}
			{@const g = gradientFor(item.filenames[0] ?? item.key)}
			<div
				class="card"
				class:fav={typeof bucket === 'number'}
				class:hate={bucket === 'hate'}
				style="--c0:{g[0]}; --c1:{g[1]}"
			>
				<div class="art">
					<img src={item.posterUrl} alt={item.label} />
					{#if item.filenames.length > 1}
						<span class="badge mono">{item.filenames.length}</span>
					{/if}
					{#if typeof bucket === 'number'}
						<span class="tier-flag mono">{bucket}</span>
					{/if}
				</div>
				<div class="label mono">{item.label}</div>
				<div class="seg">
					<button
						class="seg-btn love"
						class:on={typeof bucket === 'number'}
						title="Favorite"
						onclick={() => setFav(item.key)}
						{disabled}>♥</button
					>
					<button
						class="seg-btn"
						class:on={bucket === 'neutral'}
						title="No preference"
						onclick={() => (assign[item.key] = 'neutral')}
						{disabled}>–</button
					>
					<button
						class="seg-btn hate"
						class:on={bucket === 'hate'}
						title="Hate"
						onclick={() => (assign[item.key] = 'hate')}
						{disabled}>✕</button
					>
				</div>
				{#if typeof bucket === 'number'}
					<div class="tier">
						<button class="tier-btn" onclick={() => bumpTier(item.key, -1)} {disabled}>−</button>
						<span class="tier-val mono">tier {bucket}</span>
						<button class="tier-btn" onclick={() => bumpTier(item.key, 1)} {disabled}>+</button>
					</div>
				{/if}
			</div>
		{/each}
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
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
		gap: 14px;
	}
	.card {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 6px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		transition:
			border-color 0.14s ease,
			box-shadow 0.14s ease;
	}
	.card.fav {
		border-color: var(--gold);
		box-shadow: 0 0 0 1px var(--gold);
	}
	.card.hate {
		border-color: var(--bad);
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
	.tier-flag {
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
	.label {
		font-size: 11.5px;
		color: var(--muted);
		text-align: center;
	}
	.seg {
		display: grid;
		grid-template-columns: 1fr 1fr 1fr;
		gap: 4px;
	}
	.seg-btn {
		padding: 4px 0;
		border: 1px solid var(--line2);
		border-radius: 6px;
		background: var(--panel2);
		color: var(--muted);
		font-size: 13px;
		cursor: pointer;
	}
	.seg-btn:hover {
		border-color: var(--faint);
		color: var(--text);
	}
	.seg-btn.love.on {
		background: var(--gold);
		color: var(--on-gold);
		border-color: var(--gold);
	}
	.seg-btn.hate.on {
		background: var(--bad);
		color: var(--ink);
		border-color: var(--bad);
	}
	.seg-btn.on:not(.love):not(.hate) {
		background: var(--line2);
		color: var(--text);
		border-color: var(--line2);
	}
	.tier {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
	}
	.tier-btn {
		width: 22px;
		height: 22px;
		border: 1px solid var(--line2);
		border-radius: 6px;
		background: var(--panel2);
		color: var(--text);
		cursor: pointer;
		line-height: 1;
	}
	.tier-val {
		font-size: 11px;
		color: var(--muted);
		min-width: 44px;
		text-align: center;
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
