<script lang="ts">
	import { gradientFor, toneVar, type Tone } from '$lib/display';
	import type { LetterboxColumnItem } from '$lib/api/types';

	type Variant = 'candidates' | 'detected' | 'preview' | 'notlb' | 'processed';

	let {
		item,
		variant,
		selected = false,
		scanning = false,
		progress = 0,
		stage = null,
		onSelect
	}: {
		item: LetterboxColumnItem;
		variant: Variant;
		selected?: boolean;
		/** True while this movie is the one being analyzed in a running batch. */
		scanning?: boolean;
		/** Per-movie analysis progress (0–100); 0 renders as an indeterminate sweep. */
		progress?: number;
		/** Raw detector stage code for the scanning label. */
		stage?: string | null;
		onSelect: (id: number) => void;
	} = $props();

	const g = $derived(gradientFor(item.title));
	const res = $derived(item.source_height ? `${item.source_height}p` : '—');

	const sub = $derived.by(() => {
		if (variant === 'detected' && item.confidence && item.confidence !== 'none') {
			return `${item.year ?? '—'} · ${item.confidence}`;
		}
		if (variant === 'processed') {
			return 'MKV crop tag · reversible';
		}
		return `${item.year ?? '—'} · ${res}`;
	});

	// Suspected/measured aspect ratio for the candidate + detected columns.
	const arLabel = $derived.by(() => {
		if (item.aspect_label) return item.aspect_label;
		const ar = item.prefilter_aspect_ratio;
		if (ar) return `${ar.toFixed(2)}:1`;
		return res;
	});

	const notlbTone = $derived<Tone>(
		item.status === 'variable_unsafe' ? 'bad' : item.status === 'skipped' ? 'low' : 'muted'
	);

	const stageLabel = $derived.by(() => {
		switch (stage) {
			case 'started':
				return 'Starting…';
			case 'probing':
				return 'Probing file…';
			case 'analyzing':
				return 'Analyzing frames…';
			case 'consensus':
				return 'Calculating crop…';
			default:
				return stage ?? 'Analyzing…';
		}
	});
</script>

<button
	class="card"
	class:sel={selected}
	class:scanning
	onclick={() => onSelect(item.movie_id)}
	aria-current={selected ? 'true' : undefined}
>
	<span class="thumb" style="background:linear-gradient(150deg,{g[0]},{g[1]})">
		{#if scanning}<span class="scan" aria-hidden="true"></span>{/if}
	</span>
	<span class="body">
		<span class="title">{item.title}</span>
		{#if scanning}
			<span class="sub scan-label">{stageLabel}</span>
		{:else}
			<span class="sub mono">{sub}</span>
		{/if}
	</span>

	{#if scanning}
		<span class="spin" aria-hidden="true">⟳</span>
	{:else if variant === 'detected'}
		<span class="ar mono">{item.aspect_label ?? arLabel}</span>
	{:else if variant === 'preview' || variant === 'processed'}
		<span class="badge mono">⚡ Quick · Tag</span>
	{:else if variant === 'notlb'}
		<span class="dot" style="background:{toneVar(notlbTone)}"></span>
	{/if}

	{#if scanning}
		<span class="prog" aria-hidden="true">
			<span
				class="prog-fill"
				class:indet={progress <= 0}
				style="width:{Math.max(0, Math.min(100, progress))}%"
			></span>
		</span>
	{/if}
</button>

<style>
	/* Base entry styling is the original tray row, verbatim; the scanning overlay
	   (scan-line + progress) layers on without changing the resting look. */
	.card {
		position: relative;
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		/* Never let the flex-column list shrink a row: with overflow:hidden a flex
		   item's auto min-height collapses to 0, so a full list would squeeze the
		   padding out of every card. flex:none keeps each row at its natural height
		   and lets the list scroll instead. */
		flex: none;
		padding: 9px 14px;
		border: none;
		border-bottom: 1px solid var(--panel2);
		background: transparent;
		text-align: left;
		cursor: pointer;
		overflow: hidden;
	}
	.card:hover {
		background: var(--ink2);
	}
	.card.sel {
		background: var(--gold-soft);
		box-shadow: inset 2px 0 0 var(--gold);
	}
	.card.scanning {
		background: color-mix(in srgb, var(--gold) 7%, transparent);
	}
	.thumb {
		position: relative;
		flex: none;
		width: 26px;
		height: 40px;
		border-radius: 4px;
		border: 1px solid var(--line);
		overflow: hidden;
	}
	/* Letterbox "scan line": a bright bar sweeping the frame top→bottom. */
	.scan {
		position: absolute;
		inset: 0;
		background: linear-gradient(
			to bottom,
			transparent 0%,
			color-mix(in srgb, var(--gold) 85%, transparent) 48%,
			color-mix(in srgb, var(--gold) 95%, transparent) 50%,
			color-mix(in srgb, var(--gold) 85%, transparent) 52%,
			transparent 100%
		);
		background-size: 100% 55%;
		background-repeat: no-repeat;
		animation: lb-scan 1.25s cubic-bezier(0.45, 0, 0.55, 1) infinite;
		mix-blend-mode: screen;
	}
	@keyframes lb-scan {
		0% {
			background-position: 0 -60%;
		}
		100% {
			background-position: 0 160%;
		}
	}
	.body {
		display: flex;
		flex-direction: column;
		min-width: 0;
		flex: 1;
	}
	.title {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.sub {
		font-size: 11px;
		color: var(--faint);
		text-transform: capitalize;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.sub.scan-label {
		color: var(--gold);
		font-family: var(--font-sans);
		text-transform: none;
	}
	.ar {
		flex: none;
		font-size: 11px;
		color: var(--warn);
	}
	.badge {
		flex: none;
		font-size: 10.5px;
		color: var(--info);
		background: color-mix(in srgb, var(--info) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--info) 28%, transparent);
		padding: 2px 7px;
		border-radius: 6px;
		white-space: nowrap;
	}
	.dot {
		flex: none;
		width: 7px;
		height: 7px;
		border-radius: 50%;
	}
	.spin {
		flex: none;
		display: inline-block;
		color: var(--gold);
		font-size: 12px;
		animation: lb-spin 1s linear infinite;
	}
	@keyframes lb-spin {
		to {
			transform: rotate(360deg);
		}
	}
	/* Per-movie progress, pinned to the entry's bottom edge while scanning. */
	.prog {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 3px;
		background: color-mix(in srgb, var(--gold) 18%, transparent);
		overflow: hidden;
	}
	.prog-fill {
		display: block;
		height: 100%;
		background: var(--gold);
		transition: width 0.4s ease;
	}
	.prog-fill.indet {
		width: 35% !important;
		animation: lb-indet 1.4s ease-in-out infinite;
	}
	@keyframes lb-indet {
		0% {
			transform: translateX(-110%);
		}
		100% {
			transform: translateX(320%);
		}
	}
	.mono {
		font-family: var(--font-mono);
	}

	@media (prefers-reduced-motion: reduce) {
		.scan,
		.spin,
		.prog-fill.indet {
			animation: none;
		}
		.scan {
			background-position: 0 50%;
			opacity: 0.6;
		}
	}
</style>
