<script lang="ts">
	import { gradientFor, posterStatusMeta } from '$lib/display';
	import type { PosterStatus } from '$lib/api/types';
	import StatusDot from './StatusDot.svelte';

	let {
		title,
		year,
		posterStatus,
		posterUrl = null,
		imageAlt = title,
		gradientKey = title,
		centerTitle = false,
		rounded = true,
		fallbackStyle = 'gradient',
		fallbackPlacement,
		eager = false
	}: {
		title: string;
		year?: number | null;
		posterStatus?: PosterStatus;
		posterUrl?: string | null;
		/** Accessible description for a deployed image; fallback copy remains `title`. */
		imageAlt?: string;
		/** Stable seed for the fallback gradient when the visible title is abbreviated. */
		gradientKey?: string;
		/** Backwards-compatible alias for a centered fallback. */
		centerTitle?: boolean;
		rounded?: boolean;
		/** Use a quiet solid placeholder where a decorative gradient would compete with nearby art. */
		fallbackStyle?: 'gradient' | 'plain';
		/** Position fallback copy independently from deployed-image alt text. */
		fallbackPlacement?: 'bottom-left' | 'center' | 'hidden';
		/** Load immediately at high priority — for the page's focal poster only;
		 *  grids stay lazy so offscreen rows don't compete for connections. */
		eager?: boolean;
	} = $props();

	const g = $derived(gradientFor(gradientKey));
	const status = $derived(posterStatus ? posterStatusMeta[posterStatus] : null);
	const placement = $derived(fallbackPlacement ?? (centerTitle ? 'center' : 'bottom-left'));

	let imgFailed = $state(false);
	let imgLoaded = $state(false);
	let attemptedPosterUrl = $state<string | null | undefined>(undefined);
	const showImg = $derived(!!posterUrl && !imgFailed);

	$effect(() => {
		// A component can be reused for another record after filtering or navigation.
		// Give a new URL a fresh load attempt instead of retaining a previous failure.
		if (posterUrl !== attemptedPosterUrl) {
			attemptedPosterUrl = posterUrl;
			imgFailed = false;
			imgLoaded = false;
		}
	});

	// A cache-complete image can finish before Svelte attaches the onload
	// listener; the action catches that case so the fade-in still resolves.
	function trackLoad(node: HTMLImageElement) {
		if (node.complete && node.naturalWidth > 0) imgLoaded = true;
	}
</script>

<div
	class="poster"
	class:flat={!rounded}
	class:centered-title={placement === 'center'}
	class:bottom-left-title={placement === 'bottom-left'}
	class:plain-fallback={fallbackStyle === 'plain' && !showImg}
	style="--c0:{g[0]}; --c1:{g[1]}; --accent:{g[2]}"
>
	{#if showImg}
		<img
			src={posterUrl}
			alt={imageAlt}
			class="cover"
			class:loaded={imgLoaded}
			loading={eager ? 'eager' : 'lazy'}
			fetchpriority={eager ? 'high' : undefined}
			decoding="async"
			use:trackLoad
			onload={() => (imgLoaded = true)}
			onerror={() => (imgFailed = true)}
		/>
	{/if}
	{#if status}
		<div class="badges"><StatusDot tone={status.tone} title={status.label} /></div>
	{/if}
	{#if !showImg && placement !== 'hidden'}
		<div class="meta">
			<div class="title" style:color={fallbackStyle === 'plain' ? 'var(--muted)' : g[2]}>
				{title}
			</div>
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
	.poster.plain-fallback {
		background: var(--ink2);
	}
	/* The gradient behind stays visible until the image has pixels, then a short
	   fade replaces the old white pop-in. */
	.cover {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		opacity: 0;
		transition: opacity 160ms ease-out;
	}
	.cover.loaded {
		opacity: 1;
	}
	.badges {
		display: flex;
		align-items: center;
		gap: 6px;
		position: relative;
		z-index: 1;
	}
	.meta {
		margin-top: auto;
		text-shadow: 0 1px 6px var(--poster-shade);
		position: relative;
		z-index: 1;
	}
	.poster.centered-title .meta {
		position: absolute;
		inset: 0;
		display: grid;
		place-content: center;
		padding: 8px;
		text-align: center;
	}
	.poster.plain-fallback .meta {
		text-shadow: none;
	}
	.poster.plain-fallback .year {
		color: var(--muted);
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
