<script lang="ts">
	import { gradientFor } from '$lib/display';
	import type { CandidateView } from '$lib/api/types';

	let {
		members,
		selectable = true,
		inspected = false,
		onSelect,
		onToggle
	}: {
		members: CandidateView[];
		selectable?: boolean;
		/** Ring highlight when this stack's representative is the inspected poster. */
		inspected?: boolean;
		onSelect?: (c: CandidateView) => void;
		onToggle?: () => void;
	} = $props();

	const count = $derived(members.length);
	const representative = $derived(members[0]);
	const showBadge = $derived(count > 1);
	const g = $derived(gradientFor(representative?.orig_filename ?? ''));
	const tag = $derived(representative.stack_rank != null ? String(representative.stack_rank) : '');
	let imgFailed = $state(false);
</script>

<div
	class="stack-wrap"
	onclick={() => {
		onSelect?.(representative);
		onToggle?.();
	}}
	onkeydown={(e) => {
		if (e.key === 'Enter') {
			onSelect?.(representative);
			onToggle?.();
		}
	}}
	role="button"
	tabindex={selectable ? 0 : -1}
	style="--c0:{g[0]}; --c1:{g[1]}"
>
	<div class="art" class:inspected>
		{#if !imgFailed}
			<img
				src={representative.poster_url}
				alt={representative.orig_filename}
				onerror={() => (imgFailed = true)}
			/>
		{/if}
		{#if representative.final_score != null}
			<div class="score mono">{representative.final_score.toFixed(3)}</div>
		{/if}
	</div>
	<div class="cap">
		<span class="cap-main mono">{tag}</span>
	</div>
	{#if showBadge}
		<span class="stack-badge mono">{count}</span>
	{/if}
</div>

<style>
	.stack-wrap {
		position: relative;
		cursor: pointer;
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 0;
		border: none;
		background: transparent;
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
	.stack-wrap:hover .art {
		transform: translateY(-2px);
		border-color: var(--gold);
		box-shadow: 0 6px 18px var(--shadow);
	}
	.art.inspected {
		border-color: var(--info);
		box-shadow: 0 0 0 2px color-mix(in srgb, var(--info) 60%, transparent);
	}
	img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
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
	}
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
