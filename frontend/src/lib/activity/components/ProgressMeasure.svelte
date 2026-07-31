<script lang="ts">
	import type { ProgressMeasurement } from '../types';

	let {
		measurement,
		fallbackLabel,
		label: labelOverride = null,
		prominent = false,
		settled = false,
		tone = null
	}: {
		measurement: ProgressMeasurement;
		fallbackLabel: string;
		/** Wins over the server-supplied label — the caller knows something better. */
		label?: string | null;
		prominent?: boolean;
		settled?: boolean;
		/** Colours the fill with the outcome once there is one. Null keeps the
		 *  in-flight gold, which is the only honest colour for unfinished work. */
		tone?: string | null;
	} = $props();

	const label = $derived(labelOverride ?? measurement.label ?? fallbackLabel);
	const count = $derived(
		measurement.completed != null && measurement.total != null
			? `${measurement.completed} / ${measurement.total}${measurement.unit ? ` ${measurement.unit}` : ''}`
			: null
	);
</script>

<div class="measurement" data-mode={measurement.mode} data-tone={tone}>
	<div class="measure-label" class:prominent>
		<span>{label}</span>
		{#if count}<span class="count">{count}</span>{/if}
	</div>
	{#if measurement.mode === 'determinate' && measurement.percent != null}
		<div
			class="track"
			role="progressbar"
			aria-label={label}
			aria-valuemin="0"
			aria-valuemax="100"
			aria-valuenow={measurement.percent}
		>
			<div class="fill" style:width={`${measurement.percent}%`}></div>
		</div>
	{:else if measurement.mode === 'indeterminate' && !settled}
		<!-- A settled indeterminate measure has no position to draw, and the scanning
		     track reads as work still running. The label alone is the honest render. -->
		<div class="track indeterminate" role="progressbar" aria-label={label}>
			<div class="fill"></div>
		</div>
	{/if}
</div>

<style>
	.measurement {
		display: grid;
		gap: 6px;
	}
	.measure-label {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		color: var(--muted);
		font-size: 12px;
	}
	/* The stage is the headline fact about a running job, so when the caller hands
	   one over it outranks the surrounding chrome instead of matching it. */
	.prominent > span:first-child {
		color: var(--text);
		font-size: 14px;
		font-weight: 600;
	}
	.count {
		flex: none;
		font-family: var(--font-mono);
		color: var(--text);
	}
	.track {
		height: 7px;
		overflow: hidden;
		border-radius: 99px;
		background: var(--line);
	}
	.fill {
		height: 100%;
		border-radius: inherit;
		background: var(--fill-color, var(--gold));
		transition: width 0.25s ease;
	}
	/* A finished bar carries the verdict: green when everything landed, amber when only
	   some of it did, red where it stopped. A failure keeps its stopping point rather
	   than snapping to 100%, so the colour is what says it did not finish. */
	.measurement[data-tone='positive'] {
		--fill-color: var(--good);
	}
	.measurement[data-tone='negative'] {
		--fill-color: var(--bad);
	}
	.measurement[data-tone='neutral'] {
		--fill-color: var(--muted);
	}
	.indeterminate .fill {
		width: 35%;
		animation: activity-scan 1.4s ease-in-out infinite;
	}
	@keyframes activity-scan {
		from {
			transform: translateX(-110%);
		}
		to {
			transform: translateX(310%);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.indeterminate .fill {
			width: 100%;
			opacity: 0.55;
			animation: none;
		}
	}
</style>
