<script lang="ts">
	import { gradientFor } from '$lib/display';
	import type { CandidateView } from '$lib/api/types';
	import { rejectionTag } from '$lib/pipeline/ocr-display';

	let {
		candidate,
		kind = 'ranked',
		selectable = true,
		selected = false,
		inspected = false,
		badgeText = null,
		onSelect,
		accent = '',
		onCollapse
	}: {
		candidate: CandidateView;
		kind?: 'ranked' | 'rejected';
		selectable?: boolean;
		selected?: boolean;
		/** Ring highlight when this tile is the one the hero inspector is showing. */
		inspected?: boolean;
		badgeText?: string | null;
		onSelect?: (c: CandidateView) => void;
		/** Optional CSS color for a left-edge accent (used by expanded stacks). */
		accent?: string;
		/** When set, a collapse button appears right-aligned in the caption row. */
		onCollapse?: () => void;
	} = $props();

	const g = $derived(gradientFor(candidate.orig_filename));
	// "1A" when stacked, else "#rank". Auto-pick is the top stack's A (which,
	// under the robust stack score, may not be global rank 1).
	const stacked = $derived(candidate.stack_rank != null && candidate.stack_label != null);
	const tag = $derived(
		stacked
			? `${candidate.stack_rank}${candidate.stack_label}`
			: candidate.rank != null
				? `#${candidate.rank}`
				: ''
	);
	const isAutoPick = $derived(
		kind === 'ranked' &&
			(stacked ? candidate.stack_rank === 1 && candidate.stack_pos === 1 : candidate.rank === 1)
	);
	let imgFailed = $state(false);

	const reason = $derived(rejectionTag(candidate));
	const reasonDetail = $derived(candidate.rejection_explanation ?? reason);
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
	title={kind === 'rejected' ? reasonDetail : stacked ? `Stack ${tag}` : `Rank ${candidate.rank}`}
	style="--c0:{g[0]}; --c1:{g[1]}; --accent:{g[2]}; --group-accent:{accent}"
>
	<div class="art">
		{#if !imgFailed}
			<img
				src={candidate.poster_url}
				alt={candidate.orig_filename}
				loading="lazy"
				onerror={() => (imgFailed = true)}
			/>
		{/if}
		{#if kind === 'ranked' && candidate.final_score != null}
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
		{#if kind === 'ranked'}
			<span class="cap-main">{stacked ? tag : `Rank ${candidate.rank}`}</span>
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
