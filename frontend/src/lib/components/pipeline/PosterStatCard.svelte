<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import { toneVar, type Tone } from '$lib/display';

	let {
		label,
		value,
		sub,
		bar,
		barTone,
		tone = 'gold',
		icon,
		split,
		bars,
		href,
		hint,
		emphasis = 'quiet'
	}: {
		label: string;
		value: string | number;
		sub?: string;
		bar?: number;
		/** Health of the bar, when that differs from the card's identity tone — a
		 *  Deployed card stays green even while the coverage it reports is poor. */
		barTone?: Tone;
		tone?: Tone;
		icon?: string;
		/** The card's figure broken into named parts, carried at full size beneath it —
		 *  a television count is only useful once you know how it divides. */
		split?: { label: string; value: number }[];
		/** Several named coverage bars in one card. `value` is the percentage the bar
		 *  fills to; `display` is what the row reads out, when a count says more. */
		bars?: { label: string; value: number; tone?: Tone; display?: string | number }[];
		/** When set the whole card becomes a link into the matching workspace tab. */
		href?: string;
		/** Accessible name for the link — say where it goes, not just what it counts. */
		hint?: string;
		/** `loud` adds the tinted wash and coloured rail. Reserve it for cards that
		 *  want acting on: twelve loud cards mean none of them reads as urgent. */
		emphasis?: 'loud' | 'quiet';
	} = $props();

	const isZero = $derived(value === 0 || value === '0');
	const hasMeta = $derived(Boolean(split?.length || bar != null || bars?.length));
</script>

<svelte:element
	this={href ? 'a' : 'div'}
	{href}
	aria-label={href ? (hint ?? `${label}: ${value}`) : undefined}
	class="card mq-rise"
	class:linked={href}
	class:loud={emphasis === 'loud'}
	class:zero={isZero}
	style="--c:{toneVar(tone)}"
>
	<div class="rail"></div>
	<div class="top">
		<div class="label">{label}</div>
		{#if icon}
			<span class="chip"><Icon name={icon} size={15} /></span>
		{/if}
	</div>
	<div class="value">{value}</div>
	{#if sub}<div class="sub">{sub}</div>{/if}
	<!-- The split and the meters sit at the foot of the card, so a row of cards
	     shares one baseline however tall the tallest card grows. -->
	{#if hasMeta}
		<div class="meta">
			{#if bar != null}
				<div class="bar"><ProgressBar value={bar} tone={barTone ?? tone} /></div>
			{/if}
			{#if bars?.length}
				<div class="bars">
					{#each bars as row (row.label)}
						<div class="bar-row">
							<span class="bar-label">{row.label}</span>
							<span class="bar-read" style="--p:{toneVar(row.tone ?? tone)}"
								>{row.display ?? `${row.value}%`}</span
							>
						</div>
						<ProgressBar value={row.value} tone={row.tone ?? tone} height={5} />
					{/each}
				</div>
			{/if}
			{#if split?.length}
				<div class="split">
					{#each split as part (part.label)}
						<div class="split-cell" class:zero={part.value === 0}>
							<div class="split-value">{part.value}</div>
							<div class="split-label">{part.label}</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
	{#if href}<span class="go" aria-hidden="true"><Icon name="chevron" size={13} /></span>{/if}
</svelte:element>

<style>
	/* Each card keeps its tone in the border and icon; the tinted wash is reserved
	   for hover so a full grid reads as calm until you point at something. */
	.card {
		position: relative;
		isolation: isolate;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		background: var(--panel);
		border: 1px solid color-mix(in srgb, var(--c) 16%, var(--line));
		border-radius: var(--radius);
		padding: 13px 15px 14px;
		transition:
			transform 0.15s ease,
			box-shadow 0.15s ease,
			border-color 0.15s ease;
	}
	.card.loud {
		border-color: color-mix(in srgb, var(--c) 30%, var(--line));
	}
	/* z-index -1 keeps the wash above the card background but under the content,
	   and opacity is what makes a gradient fade — gradients cannot transition. */
	.card::before {
		content: '';
		position: absolute;
		inset: 0;
		z-index: -1;
		background: linear-gradient(
			160deg,
			color-mix(in srgb, var(--c) 15%, transparent),
			transparent 64%
		);
		opacity: 0;
		transition: opacity 0.22s ease;
		pointer-events: none;
	}
	.card.linked:hover::before,
	.card.linked:focus-visible::before {
		opacity: 1;
	}
	.card.linked:hover {
		transform: translateY(-1px);
		box-shadow: 0 8px 22px var(--shadow);
		border-color: color-mix(in srgb, var(--c) 45%, var(--line));
	}
	.card.linked {
		color: inherit;
		cursor: pointer;
	}
	/* Keeps the last column clear of the chevron parked in the bottom-right corner. */
	.card.linked .split-cell:last-child {
		padding-right: 24px;
	}
	/* The chevron is the only affordance distinguishing a link card from a plain
	   one, so it brightens on hover instead of appearing only then. */
	.go {
		position: absolute;
		right: 11px;
		bottom: 10px;
		display: inline-flex;
		color: var(--faint2);
		transition:
			color 0.15s ease,
			transform 0.15s ease;
	}
	.card.linked:hover .go {
		color: var(--c);
		transform: translateX(2px);
	}
	.card.linked:focus-visible {
		outline: 2px solid var(--c);
		outline-offset: 2px;
	}
	.rail {
		position: absolute;
		inset: 0 0 auto 0;
		height: 3px;
		background: linear-gradient(90deg, var(--c), color-mix(in srgb, var(--c) 25%, transparent));
	}
	/* Tone is what the card *measures*, not how alarmed to be: Missing stays red so
	   the row reads by colour, but an empty Missing card fades rather than shouts. */
	.card.zero .rail {
		opacity: 0.4;
	}
	/* Holds the chip's height whether or not a chip is showing, so labels and values
	   stay on one line across the row as icons come and go. */
	.top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		min-height: 24px;
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
	/* A card with a count in it names itself in its own tone; a zero stays grey so
	   the row reads at a glance. Loud cards push further, since the tinted wash
	   now only appears on hover. */
	.card:not(.zero) .label {
		color: color-mix(in srgb, var(--c) 72%, var(--muted));
	}
	.card.loud:not(.zero) .label {
		color: color-mix(in srgb, var(--c) 90%, var(--muted));
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
		background: color-mix(in srgb, var(--c) 9%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 20%, transparent);
	}
	.card.loud .chip {
		background: color-mix(in srgb, var(--c) 16%, transparent);
		border-color: color-mix(in srgb, var(--c) 32%, transparent);
	}
	.value {
		font-family: var(--font-mono);
		font-size: 26px;
		font-weight: 600;
		line-height: 1.1;
		margin-top: 6px;
		color: var(--text);
	}
	/* A zero means "nothing here" — dim it so a row of zeroes recedes instead of
	   competing with the counts that do want acting on. Real figures stay bright. */
	.card.zero .value {
		color: var(--faint);
	}
	.sub {
		font-size: 12px;
		color: var(--muted);
		margin-top: 3px;
	}
	.meta {
		margin-top: auto;
		padding-top: 6px;
	}
	/* The split is a second reading of the same figure, not a footnote to it: same
	   size, same face, divided into its parts across the foot of the card. */
	.split {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: minmax(0, 1fr);
		gap: 16px;
		padding-top: 12px;
	}
	.split-value {
		font-family: var(--font-mono);
		font-size: 26px;
		font-weight: 600;
		line-height: 1.1;
		color: var(--text);
	}
	.split-cell.zero .split-value {
		color: var(--faint);
	}
	.split-label {
		margin-top: 3px;
		font-size: 10.5px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--muted);
	}
	.bar {
		margin-top: 10px;
	}
	.bars {
		display: grid;
		gap: 3px;
		margin-top: 9px;
	}
	.bar-row {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 8px;
	}
	.bar-row:not(:first-child) {
		margin-top: 5px;
	}
	/* Named like the split's labels, since both are the same idea: which part of the
	   figure above this row accounts for. */
	.bar-label {
		font-size: 10.5px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--muted);
	}
	/* The readout carries the health reading the green card deliberately doesn't;
	   pulled toward the foreground so small coloured text stays legible in both themes. */
	.bar-read {
		--p: var(--text);
		color: color-mix(in srgb, var(--p) 70%, var(--text));
		font: 600 13px/1 var(--font-mono);
	}
</style>
