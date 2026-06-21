<script lang="ts">
	import { gradientFor, posterStatusMeta } from '$lib/display';
	import type { HdrKind, PosterStatus } from '$lib/api/types';
	import StatusDot from './StatusDot.svelte';
	import HdrBadge from './HdrBadge.svelte';

	let {
		title,
		year,
		posterStatus,
		posterUrl = null,
		hdr = null,
		rounded = true
	}: {
		title: string;
		year?: number | null;
		posterStatus?: PosterStatus;
		posterUrl?: string | null;
		hdr?: HdrKind | null;
		rounded?: boolean;
	} = $props();

	const g = $derived(gradientFor(title));
	const status = $derived(posterStatus ? posterStatusMeta[posterStatus] : null);

	let imgFailed = $state(false);
	const showImg = $derived(!!posterUrl && !imgFailed);
</script>

<div class="poster" class:flat={!rounded} style="--c0:{g[0]}; --c1:{g[1]}; --accent:{g[2]}">
	{#if showImg}
		<img src={posterUrl} alt={title} class="cover" onerror={() => (imgFailed = true)} />
	{/if}
	<div class="badges">
		{#if status}<StatusDot tone={status.tone} title={status.label} />{/if}
		<span class="spacer"></span>
		<HdrBadge kind={hdr} />
	</div>
	{#if !showImg}
		<div class="meta">
			<div class="title" style="color:{g[2]}">{title}</div>
			{#if year}<div class="year">{year}</div>{/if}
		</div>
	{/if}
</div>

<style>
	.poster {
		aspect-ratio: 2 / 3;
		border-radius: var(--radius-sm);
		background: linear-gradient(165deg, var(--c0), var(--c1));
		border: 1px solid var(--line);
		padding: 7px;
		display: flex;
		flex-direction: column;
		justify-content: space-between;
		overflow: hidden;
		position: relative;
	}
	.poster.flat {
		border-radius: 0;
	}
	.cover {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.badges {
		display: flex;
		align-items: center;
		gap: 6px;
		position: relative;
		z-index: 1;
	}
	.spacer {
		flex: 1;
	}
	.meta {
		text-shadow: 0 1px 6px var(--poster-shade);
		position: relative;
		z-index: 1;
	}
	.title {
		font-size: 12px;
		font-weight: 650;
		line-height: 1.2;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.year {
		font-family: var(--font-mono);
		font-size: 10px;
		color: rgba(255, 255, 255, 0.6);
		margin-top: 2px;
	}
</style>
