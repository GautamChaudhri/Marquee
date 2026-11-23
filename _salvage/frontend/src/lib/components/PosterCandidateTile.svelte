<script lang="ts">
	import { gradientFor } from '$lib/display';
	import type { CandidateView } from '$lib/api/types';
	import { rejectionTag } from '$lib/pipeline/ocr-display';
	import { rankCaption } from '$lib/pipeline/rank-display';

	let {
		candidate,
		kind = 'ranked',
		selectable = true,
		selected = false,
		inspected = false,
		isPersistedAutoPick = false,
		badgeText = null,
		displayRank = null,
		rankSuffix = null,
		onSelect,
		accent = '',
		onCollapse
	}: {
		candidate: CandidateView;
		kind?: 'ranked' | 'candidate' | 'rejected';
		selectable?: boolean;
		selected?: boolean;
		/** Ring highlight when this tile is the one the hero inspector is showing. */
		inspected?: boolean;
		/** True only when this filename matches the run's persisted auto-pick. */
		isPersistedAutoPick?: boolean;
		badgeText?: string | null;
		/** Position in the current flat grid, when it differs from the archived candidate rank. */
		displayRank?: number | null;
		/** Variant suffix (A, B, C…) for a currently expanded stack. */
		rankSuffix?: string | null;
		onSelect?: (c: CandidateView) => void;
		/** Optional CSS color for a left-edge accent (used by expanded stacks). */
		accent?: string;
		/** When set, a collapse button appears right-aligned in the caption row. */
		onCollapse?: () => void;
	} = $props();

	const g = $derived(gradientFor(candidate.orig_filename));
	// "1A" when stacked, else "#rank". The parent supplies auto-pick state
	// from the canonical persisted run projection; a display rank is never enough.
	const stacked = $derived(candidate.stack_rank != null && candidate.stack_label != null);
	const tag = $derived(
		stacked
			? `${candidate.stack_rank}${candidate.stack_label}`
			: candidate.rank != null
				? `#${candidate.rank}`
				: ''
	);
	const visibleRank = $derived(displayRank ?? candidate.rank);
	const visibleRankCaption = $derived(rankCaption(visibleRank, rankSuffix));
	const designLabel = $derived(stacked ? `Design ${tag}` : null);
	const isRanked = $derived(kind === 'ranked');
	const isAutoPick = $derived(isRanked && isPersistedAutoPick);
	let imgFailed = $state(false);
	let attemptedUrl = $state<string | null | undefined>(undefined);

	$effect(() => {
		// Tiles are recycled across filter/tab changes; a new URL deserves a
		// fresh load attempt instead of a retained failure.
		if (candidate.poster_url !== attemptedUrl) {
			attemptedUrl = candidate.poster_url;
			imgFailed = false;
		}
	});

	const reason = $derived(rejectionTag(candidate));
	const reasonDetail = $derived(candidate.rejection_explanation ?? reason);
	const tileTitle = $derived(
		kind === 'rejected'
			? reasonDetail
			: kind === 'candidate'
				? 'Review candidate'
				: designLabel
					? `${visibleRankCaption} · ${designLabel}`
					: visibleRankCaption
	);
</script>

<button
	class="tile"
	class:auto={isAutoPick}
	class:rejected={kind === 'rejected'}
	class:accented={accent !== ''}
	class:selected
	class:inspected
	disabled={!selectable}
	onclick={() => onSelect?.(candidate)}
	title={tileTitle}
	style="--c0:{g[0]}; --c1:{g[1]}; --accent:{g[2]}; --group-accent:{accent}"
>
	<div class="art">
		{#if !imgFailed}
			<img
				src={candidate.poster_url}
				alt={candidate.orig_filename}
				loading="lazy"
				decoding="async"
				onerror={() => (imgFailed = true)}
			/>
		{/if}
		{#if isRanked && candidate.final_score != null}
			<div class="score mono">{candidate.final_score.toFixed(3)}</div>
		{/if}
		{#if selected}
			<div class="selected-mark mono">✓</div>
		{/if}
		{#if badgeText}
			<div class="status-badge">{badgeText}</div>
		{/if}
	</div>
	<div class="cap" class:has-collapse={onCollapse != null}>
		{#if isRanked}
			<span class="cap-main">{visibleRankCaption}</span>
		{:else if kind === 'candidate'}
			<span class="cap-main">Candidate</span>
		{:else}
			<span class="cap-main bad-text" title={reasonDetail}>{reason}</span>
		{/if}
		{#if onCollapse}
			<span
				class="cap-collapse"
				role="button"
				tabindex="0"
				onclick={(e) => {
					e.stopPropagation();
					onCollapse();
				}}
				onkeydown={(e) => {
					if (e.key === 'Enter') {
						e.stopPropagation();
						onCollapse();
					}
				}}
				title="Collapse this stack"
			>
				<svg
					width="12"
					height="12"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="2.5"
					stroke-linecap="round"
					stroke-linejoin="round"><polyline points="15 18 9 12 15 6" /></svg
				>
			</span>
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
	.tile.selected .art {
		border-color: var(--gold);
		box-shadow: 0 0 0 2px color-mix(in srgb, var(--gold) 55%, transparent);
	}
	.tile.inspected .art {
		border-color: var(--info);
		box-shadow: 0 0 0 2px color-mix(in srgb, var(--info) 60%, transparent);
	}
	.tile.rejected .art {
		opacity: 0.82;
	}
	.tile.rejected:not(:disabled):hover .art {
		opacity: 1;
		border-color: var(--line2);
	}
	.tile.accented {
		background: color-mix(in srgb, var(--group-accent) 4%, transparent);
	}
	.tile.accented .art {
		border-left: 3px solid var(--group-accent);
		border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
	}
	.tile.accented .cap-main {
		color: var(--group-accent);
		font-weight: 600;
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
	.selected-mark {
		position: absolute;
		top: 6px;
		right: 6px;
		min-width: 18px;
		height: 18px;
		padding: 0 5px;
		border-radius: 999px;
		background: color-mix(in srgb, var(--gold) 82%, transparent);
		color: var(--on-gold);
		font-size: 11px;
		font-weight: 700;
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 2;
	}
	.status-badge {
		position: absolute;
		top: 6px;
		left: 6px;
		max-width: calc(100% - 36px);
		padding: 2px 7px;
		border-radius: 999px;
		background: color-mix(in srgb, var(--warn) 70%, transparent);
		color: var(--ink);
		font-size: 10px;
		font-weight: 700;
		line-height: 1.2;
		z-index: 2;
	}
	.cap {
		min-width: 0;
	}
	.cap.has-collapse {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.cap.has-collapse .cap-main {
		flex: 1;
	}
	.cap-collapse {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 18px;
		height: 18px;
		padding: 0;
		border: 1px solid var(--line);
		border-radius: 4px;
		background: var(--panel2);
		color: var(--muted);
		cursor: pointer;
		flex-shrink: 0;
		transition:
			color 0.12s ease,
			border-color 0.12s ease;
	}
	.cap-collapse:hover {
		color: var(--text);
		border-color: var(--gold);
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
