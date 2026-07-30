<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import { toneVar, type Tone } from '$lib/display';

	let {
		label,
		value,
		sub,
		bar,
		tone = 'gold',
		icon,
		breakdown
	}: {
		label: string;
		value: string | number;
		sub?: string;
		bar?: number;
		tone?: Tone;
		icon?: string;
		breakdown?: { label: string; value: string | number }[];
	} = $props();
</script>

<div class="card mq-rise" style="--c:{toneVar(tone)}">
	<div class="rail"></div>
	<div class="top">
		<div class="label">{label}</div>
		{#if icon}
			<span class="chip"><Icon name={icon} size={15} /></span>
		{/if}
	</div>
	<div class="value">{value}</div>
	{#if sub}<div class="sub">{sub}</div>{/if}
	{#if breakdown?.length}
		<div class="breakdown">
			{#each breakdown as part, i (part.label)}
				{#if i > 0}<span class="dot">·</span>{/if}
				<span class="part"><b>{part.value}</b> {part.label}</span>
			{/each}
		</div>
	{/if}
	{#if bar != null}<div class="bar"><ProgressBar value={bar} {tone} /></div>{/if}
</div>

<style>
	.card {
		position: relative;
		overflow: hidden;
		background: linear-gradient(
			160deg,
			color-mix(in srgb, var(--c) 12%, var(--panel)),
			var(--panel) 62%
		);
		border: 1px solid color-mix(in srgb, var(--c) 26%, var(--line));
		border-radius: var(--radius);
		padding: 13px 15px 14px;
		transition:
			transform 0.15s ease,
			box-shadow 0.15s ease,
			border-color 0.15s ease;
	}
	.card:hover {
		transform: translateY(-1px);
		box-shadow: 0 8px 22px var(--shadow);
		border-color: color-mix(in srgb, var(--c) 42%, var(--line));
	}
	.rail {
		position: absolute;
		inset: 0 0 auto 0;
		height: 3px;
		background: linear-gradient(90deg, var(--c), color-mix(in srgb, var(--c) 25%, transparent));
	}
	.top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}
	.label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 24px;
		flex: none;
		border-radius: 7px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 14%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 24%, transparent);
	}
	.value {
		font-family: var(--font-mono);
		font-size: 26px;
		font-weight: 600;
		line-height: 1.1;
		margin-top: 6px;
		color: var(--text);
	}
	.sub {
		font-size: 12px;
		color: var(--muted);
		margin-top: 3px;
	}
	.breakdown {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 5px;
		margin-top: 5px;
		font-size: 11.5px;
		color: var(--faint);
	}
	.breakdown b {
		color: var(--muted);
		font-family: var(--font-mono);
		font-weight: 600;
	}
	.dot {
		color: var(--line2);
	}
	.bar {
		margin-top: 10px;
	}
</style>
