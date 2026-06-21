<script lang="ts">
	import { gradientFor } from '$lib/display';
	import type { CandidateView } from '$lib/api/types';

	let {
		candidate,
		kind = 'ranked',
		selectable = true,
		onSelect
	}: {
		candidate: CandidateView;
		kind?: 'ranked' | 'rejected';
		selectable?: boolean;
		onSelect?: (c: CandidateView) => void;
	} = $props();

	const g = $derived(gradientFor(candidate.orig_filename));
	const isAutoPick = $derived(kind === 'ranked' && candidate.rank === 1);
	let imgFailed = $state(false);

	const reason = $derived(
		candidate.rejection_explanation ?? candidate.rejection_reason ?? candidate.gate_reason ?? ''
	);
</script>

<button
	class="tile"
	class:auto={isAutoPick}
	class:rejected={kind === 'rejected'}
	disabled={!selectable}
	onclick={() => onSelect?.(candidate)}
	title={kind === 'rejected' ? reason : `Rank ${candidate.rank}`}
	style="--c0:{g[0]}; --c1:{g[1]}; --accent:{g[2]}"
>
	<div class="art">
		{#if !imgFailed}
			<img
				src={candidate.poster_url}
				alt={candidate.orig_filename}
				onerror={() => (imgFailed = true)}
			/>
		{/if}
		<div class="top">
			{#if kind === 'ranked'}
				<span class="rank">#{candidate.rank}</span>
				{#if isAutoPick}<span class="auto-tag">AUTO</span>{/if}
			{/if}
		</div>
		{#if kind === 'ranked' && candidate.final_score != null}
			<div class="score mono">{candidate.final_score.toFixed(3)}</div>
		{/if}
	</div>
	<div class="cap">
		{#if kind === 'ranked'}
			<span class="cap-main">Rank {candidate.rank}</span>
		{:else}
			<span class="cap-main bad-text" title={reason}>{reason || 'Rejected'}</span>
		{/if}
	</div>
</button>

<style>
	.tile {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 0;
		border: none;
		background: transparent;
		text-align: left;
		cursor: pointer;
	}
	.tile:disabled {
		cursor: default;
	}
	.art {
		position: relative;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: linear-gradient(165deg, var(--c0), var(--c1));
		border: 1px solid var(--line);
		transition:
			transform 0.14s ease,
			border-color 0.14s ease,
			box-shadow 0.14s ease;
	}
	.tile:not(:disabled):hover .art {
		transform: translateY(-2px);
		border-color: var(--gold);
		box-shadow: 0 6px 18px var(--shadow);
	}
	.tile.auto .art {
		border-color: var(--gold);
		box-shadow: 0 0 0 1px var(--gold-deep);
	}
	.tile.rejected .art {
		opacity: 0.82;
	}
	.tile.rejected:not(:disabled):hover .art {
		opacity: 1;
		border-color: var(--line2);
	}
	img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.top {
		position: absolute;
		inset: 6px 6px auto 6px;
		display: flex;
		align-items: center;
		gap: 5px;
		z-index: 1;
	}
	.rank {
		font-family: var(--font-mono);
		font-size: 11px;
		font-weight: 700;
		padding: 1px 6px;
		border-radius: 6px;
		background: color-mix(in srgb, var(--ink) 72%, transparent);
		color: var(--text);
		backdrop-filter: blur(2px);
	}
	.auto-tag {
		font-size: 9px;
		font-weight: 700;
		letter-spacing: 0.06em;
		padding: 1px 5px;
		border-radius: 6px;
		background: var(--gold);
		color: var(--on-gold);
	}
	.score {
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
	.cap {
		min-width: 0;
	}
	.cap-main {
		display: block;
		font-size: 11.5px;
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.bad-text {
		color: var(--low);
	}
	.mono {
		font-family: var(--font-mono);
	}
</style>
