<script lang="ts">
	import { gradientFor } from '$lib/display';
	import type { CandidateView } from '$lib/api/types';

	let {
		members,
		selectable = true,
		onSelect,
		onToggle
	}: {
		members: CandidateView[];
		selectable?: boolean;
		onSelect?: (c: CandidateView) => void;
		onToggle?: () => void;
	} = $props();

	const count = $derived(members.length);
	const representative = $derived(members[0]);
	/** Show up to 3 offset ghost cards behind the front card. */
	const backCards = $derived(members.slice(1, 4));
	const g = $derived(gradientFor(representative?.orig_filename ?? ''));
	const showBadge = $derived(count > 1);
</script>

<button
	class="stack collapsed"
	onclick={() => onToggle?.()}
	style="--s0:{g[0]}; --s1:{g[1]}"
	disabled={!selectable}
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
	<!-- Count badge (only when >1) -->
	{#if showBadge}
		<span class="stack-badge mono">{count}</span>
	{/if}
</button>

<style>
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
	.stack.collapsed:disabled {
		cursor: default;
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

	.stack.collapsed:not(:disabled):hover .stack-ghost {
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
	.stack.collapsed:not(:disabled):hover .stack-front {
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

	.mono {
		font-family: var(--font-mono);
	}
</style>
