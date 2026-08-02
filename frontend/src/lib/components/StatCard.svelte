<script lang="ts">
	import ProgressBar from './ProgressBar.svelte';
	import { toneVar, type Tone } from '$lib/display';
	let {
		label,
		value,
		sub,
		note,
		bar,
		tone = 'gold'
	}: {
		label: string;
		value: string | number;
		sub?: string;
		/** One line explaining what the number actually counts. */
		note?: string;
		bar?: number;
		tone?: Tone;
	} = $props();
</script>

<div class="card">
	<div class="label">{label}</div>
	<div class="value" style="--c:{toneVar(tone)}">{value}</div>
	{#if sub}<div class="sub">{sub}</div>{/if}
	{#if bar != null}<div class="bar"><ProgressBar value={bar} {tone} /></div>{/if}
	{#if note}<p class="note">{note}</p>{/if}
</div>

<style>
	.card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 14px 16px;
	}
	.label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 600;
	}
	.value {
		font-family: var(--font-mono);
		font-size: 24px;
		font-weight: 600;
		margin-top: 4px;
		color: var(--text);
	}
	.sub {
		font-size: 12px;
		color: var(--muted);
		margin-top: 2px;
	}
	.bar {
		margin-top: 10px;
	}
	.note {
		margin: 10px 0 0;
		padding-top: 9px;
		border-top: 1px solid var(--line);
		font-size: 11.5px;
		line-height: 1.45;
		color: var(--faint);
	}
</style>
