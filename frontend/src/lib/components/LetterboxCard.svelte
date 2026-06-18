<script lang="ts">
	import { gradientFor, toneVar, type Tone } from '$lib/display';
	import type { LetterboxColumnItem } from '$lib/api/types';

	type Variant = 'candidates' | 'detected' | 'preview' | 'notlb' | 'processed';

	let {
		item,
		variant,
		selected = false,
		onSelect
	}: {
		item: LetterboxColumnItem;
		variant: Variant;
		selected?: boolean;
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
</script>

<button class="card" class:sel={selected} onclick={() => onSelect(item.movie_id)}>
	<span class="thumb" style="background:linear-gradient(150deg,{g[0]},{g[1]})"></span>
	<span class="body">
		<span class="title">{item.title}</span>
		<span class="sub mono">{sub}</span>
	</span>

	{#if variant === 'detected'}
		<span class="ar mono">{item.aspect_label ?? arLabel}</span>
	{:else if variant === 'preview' || variant === 'processed'}
		<span class="badge mono">⚡ Quick · Tag</span>
	{:else if variant === 'notlb'}
		<span class="dot" style="background:{toneVar(notlbTone)}"></span>
	{/if}
</button>

<style>
	.card {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 9px 14px;
		border: none;
		border-bottom: 1px solid var(--panel2);
		background: transparent;
		text-align: left;
		cursor: pointer;
	}
	.card:hover {
		background: var(--ink2);
	}
	.card.sel {
		background: var(--gold-soft);
		box-shadow: inset 2px 0 0 var(--gold);
	}
	.thumb {
		flex: none;
		width: 26px;
		height: 40px;
		border-radius: 4px;
		border: 1px solid var(--line);
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
	.mono {
		font-family: var(--font-mono);
	}
</style>
