<script lang="ts">
	import type { Candidate } from '#lib/api/types.ts';
	import { formatScore } from '#lib/format.ts';
	import { rankedCandidates } from './runs';

	/** A run's ranked candidates, best first, read-only: choosing one is the Review page's. */
	let { candidates }: { candidates: Candidate[] } = $props();
	const ranked = $derived(rankedCandidates(candidates));
</script>

<ol class="candidates" data-testid="candidates">
	{#each ranked as candidate (candidate.id)}
		<li class:pick={candidate.is_pick}>
			<div class="frame">
				{#if candidate.image_url}
					<img src={candidate.image_url} alt="Candidate ranked {candidate.rank}" loading="lazy" />
				{:else}
					<div class="none">No image</div>
				{/if}
				{#if candidate.is_pick}<span class="mark">Pick</span>{/if}
			</div>
			<div class="facts">
				<span class="rank">#{candidate.rank}</span>
				<span class="score" title="Final score">{formatScore(candidate.final_score)}</span>
				{#if candidate.stack}<span class="stack" title="Design stack">{candidate.stack}</span>{/if}
			</div>
		</li>
	{/each}
</ol>

<style>
	.candidates {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
		gap: 14px 12px;
	}
	li {
		display: flex;
		flex-direction: column;
		gap: 5px;
		min-width: 0;
	}
	.frame {
		position: relative;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel2);
		border: 1px solid var(--line);
	}
	.pick .frame {
		border: 2px solid var(--gold);
	}
	img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.none {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		color: var(--faint);
		font-size: 12px;
	}
	.mark {
		position: absolute;
		top: 6px;
		left: 6px;
		padding: 2px 7px;
		border-radius: var(--radius-pill);
		background: var(--gold);
		color: var(--on-gold);
		font-size: 10.5px;
		font-weight: 700;
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
	@media (max-width: 720px) {
		.candidates {
			grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
		}
	}
</style>
