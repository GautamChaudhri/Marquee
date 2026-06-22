<script lang="ts">
	import { gradientFor } from '$lib/display';
	import PosterCandidateTile from './PosterCandidateTile.svelte';
	import type { CandidateView } from '$lib/api/types';

	let {
		members,
		selectable = true,
		onSelect
	}: {
		members: CandidateView[];
		selectable?: boolean;
		onSelect?: (c: CandidateView) => void;
	} = $props();

	let expanded = $state(false);

	function toggleExpand() {
		expanded = !expanded;
	}

	const count = $derived(members.length);
	const representative = $derived(members[0]);
	// Show up to 3 offset card layers behind the front card
	const backCards = $derived(members.slice(1, 4));
	const g = $derived(gradientFor(representative?.orig_filename ?? ''));
</script>

{#if expanded}
	<!-- ── Expanded: all members in a horizontal row ── -->
	<div class="stack expanded">
		<div class="stack-fan">
			{#each members as c (c.orig_filename)}
				<PosterCandidateTile
					candidate={c}
					kind="ranked"
					{selectable}
					{onSelect}
				/>
			{/each}
		</div>
		<button class="stack-collapse-btn" onclick={toggleExpand}>
			<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
			Collapse stack ({count})
		</button>
	</div>
{:else}
	<!-- ── Collapsed: visual card stack ── -->
	<button
		class="stack collapsed"
		onclick={toggleExpand}
		style="--s0:{g[0]}; --s1:{g[1]}"
	>
		<div class="stack-pile">
			<!-- Backing cards (offset layers) -->
			{#each backCards as card, i}
				<div
					class="stack-ghost"
					style="--n:{i + 1}; --c0:{gradientFor(card.orig_filename)[0]}; --c1:{gradientFor(card.orig_filename)[1]}"
				>
					<img src={card.poster_url} alt="" loading="lazy" />
				</div>
			{/each}
			<!-- Front card -->
			<div class="stack-front">
				<img
					src={representative.poster_url}
					alt={representative.orig_filename}
					loading="lazy"
				/>
				{#if representative.final_score != null}
					<span class="stack-front-score mono">{representative.final_score.toFixed(3)}</span>
				{/if}
			</div>
		</div>
		<!-- Count badge -->
		<span class="stack-badge mono">{count}</span>
	</button>
{/if}

<style>
	/* ── Collapsed stack ── */
	.stack.collapsed {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 0;
		border: none;
		background: transparent;
		cursor: pointer;
		text-align: left;
		position: relative;
	}

	.stack-pile {
		position: relative;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: visible;
	}

	/* Ghost cards — offset behind the front */
	.stack-ghost {
		position: absolute;
		inset: 0;
		border-radius: var(--radius-sm);
		overflow: hidden;
		border: 1px solid var(--line);
		background: linear-gradient(165deg, var(--c0), var(--c1));
		transform: translate(calc(var(--n) * 5px), calc(var(--n) * 5px));
		opacity: 0.55;
		transition: transform 0.18s ease, opacity 0.18s ease;
	}
	.stack-ghost img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.stack.collapsed:hover .stack-ghost {
		opacity: 0.7;
	}

	/* Front card */
	.stack-front {
		position: relative;
		z-index: 1;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		border: 1px solid var(--line);
		background: linear-gradient(165deg, var(--s0), var(--s1));
		transition:
			transform 0.14s ease,
			border-color 0.14s ease,
			box-shadow 0.14s ease;
	}
	.stack.collapsed:hover .stack-front {
		transform: translateY(-2px);
		border-color: var(--gold);
		box-shadow: 0 6px 18px var(--shadow);
	}
	.stack-front img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.stack-front-score {
		position: absolute;
		inset: auto 6px 6px 6px;
		font-size: 11px;
		padding: 1px 6px;
		border-radius: 6px;
		background: color-mix(in srgb, var(--ink) 72%, transparent);
		color: var(--gold);
		width: fit-content;
		backdrop-filter: blur(2px);
		z-index: 1;
	}

	/* Count badge */
	.stack-badge {
		position: absolute;
		top: -6px;
		right: -6px;
		z-index: 5;
		min-width: 20px;
		height: 20px;
		padding: 0 5px;
		border-radius: 10px;
		background: var(--gold);
		color: var(--on-gold);
		font-size: 11px;
		font-weight: 700;
		display: flex;
		align-items: center;
		justify-content: center;
		box-shadow: 0 2px 6px var(--shadow);
	}

	/* ── Expanded stack ── */
	.stack.expanded {
		grid-column: 1 / -1;
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 12px;
		border: 1px solid var(--gold);
		border-radius: var(--radius-sm);
		background: var(--panel);
		animation: stack-expand 0.2s ease;
	}

	@keyframes stack-expand {
		from {
			opacity: 0.6;
			transform: scale(0.98);
		}
		to {
			opacity: 1;
			transform: scale(1);
		}
	}

	.stack-fan {
		display: flex;
		gap: 14px;
		flex-wrap: wrap;
	}

	.stack-collapse-btn {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		align-self: flex-start;
		padding: 5px 12px;
		border-radius: 7px;
		border: 1px solid var(--line);
		background: var(--panel2);
		color: var(--muted);
		font-size: 12px;
		font-weight: 550;
		cursor: pointer;
		transition:
			color 0.12s ease,
			border-color 0.12s ease;
	}
	.stack-collapse-btn:hover {
		color: var(--text);
		border-color: var(--line2);
	}

	.mono {
		font-family: var(--font-mono);
	}
</style>
