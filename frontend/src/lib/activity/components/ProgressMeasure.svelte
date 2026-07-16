<script lang="ts">
	import type { ProgressMeasurement } from '../types';

	let { measurement, fallbackLabel }: { measurement: ProgressMeasurement; fallbackLabel: string } =
		$props();

	const label = $derived(measurement.label ?? fallbackLabel);
	const count = $derived(
		measurement.completed != null && measurement.total != null
			? `${measurement.completed} / ${measurement.total}${measurement.unit ? ` ${measurement.unit}` : ''}`
			: null
	);
</script>

<div class="measurement" data-mode={measurement.mode}>
	<div class="measure-label">
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
	{:else if measurement.mode === 'indeterminate'}
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
		background: var(--gold);
		transition: width 0.25s ease;
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
