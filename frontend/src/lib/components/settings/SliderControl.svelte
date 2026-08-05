<script lang="ts">
	/**
	 * A bounded numeric knob: drag for feel, type for precision.
	 *
	 * Tuning a scorer weight is a comparative judgement — you want to see where a
	 * value sits inside its range, which a bare number box cannot show. The
	 * readout stays editable so an exact value is still one keystroke away.
	 */
	let {
		id,
		value,
		min,
		max,
		step,
		integer = false,
		disabled = false,
		label,
		onChange
	}: {
		id: string;
		value: number;
		min: number;
		max: number;
		step: number;
		integer?: boolean;
		disabled?: boolean;
		label: string;
		onChange: (value: number) => void;
	} = $props();

	// Decimals come from the step, so 0.01 shows 0.35 and 0.5 shows 2.5 — the
	// readout never invents precision the control cannot actually reach.
	const decimals = $derived.by(() => {
		if (integer) return 0;
		const text = String(step);
		const point = text.indexOf('.');
		return point === -1 ? 0 : Math.min(text.length - point - 1, 4);
	});

	const clamped = $derived(Math.min(max, Math.max(min, Number.isFinite(value) ? value : min)));
	const fraction = $derived(max > min ? (clamped - min) / (max - min) : 0);
	const display = $derived(integer ? String(clamped) : clamped.toFixed(decimals));

	function parse(raw: string): number | null {
		const parsed = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
		return Number.isFinite(parsed) ? parsed : null;
	}

	function commitSlider(event: Event) {
		const parsed = parse((event.currentTarget as HTMLInputElement).value);
		if (parsed !== null) onChange(parsed);
	}

	// The number box is not clamped while typing — that would fight the user
	// mid-keystroke ("0.0" becoming "0.01"). It is clamped on blur instead.
	function commitNumber(event: Event) {
		const parsed = parse((event.currentTarget as HTMLInputElement).value);
		if (parsed !== null) onChange(parsed);
	}

	function clampOnBlur(event: FocusEvent) {
		const parsed = parse((event.currentTarget as HTMLInputElement).value);
		if (parsed === null) onChange(clamped);
		else if (parsed < min || parsed > max) onChange(Math.min(max, Math.max(min, parsed)));
	}
</script>

<div class="slider-control" style="--fill: {(fraction * 100).toFixed(2)}%">
	<input
		type="range"
		aria-label="{label} slider"
		{min}
		{max}
		{step}
		{disabled}
		value={clamped}
		oninput={commitSlider}
	/>
	<input
		{id}
		type="number"
		class="readout"
		aria-label={label}
		{min}
		{max}
		{step}
		{disabled}
		value={display}
		oninput={commitNumber}
		onblur={clampOnBlur}
	/>
</div>

<style>
	.slider-control {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
		width: 100%;
	}

	input[type='range'] {
		flex: 1 1 auto;
		min-width: 72px;
		height: 18px;
		margin: 0;
		padding: 0;
		border: 0;
		background: transparent;
		appearance: none;
		cursor: pointer;
	}
	input[type='range']:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}

	/* The track is painted as a gradient so the filled portion reads as progress.
	   Track and thumb pseudo-elements cannot be combined in one selector list —
	   an unknown pseudo-element invalidates the whole rule — so they repeat. */
	input[type='range']::-webkit-slider-runnable-track {
		height: 6px;
		border-radius: 999px;
		background: linear-gradient(
			to right,
			var(--gold-deep) 0 var(--fill),
			var(--faint2) var(--fill) 100%
		);
	}
	input[type='range']::-moz-range-track {
		height: 6px;
		border-radius: 999px;
		background: linear-gradient(
			to right,
			var(--gold-deep) 0 var(--fill),
			var(--faint2) var(--fill) 100%
		);
	}

	input[type='range']::-webkit-slider-thumb {
		appearance: none;
		width: 14px;
		height: 14px;
		margin-top: -4px;
		border: 2px solid var(--gold);
		border-radius: 50%;
		background: var(--text);
		transition: transform 120ms ease;
	}
	input[type='range']::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border: 2px solid var(--gold);
		border-radius: 50%;
		background: var(--text);
		transition: transform 120ms ease;
	}
	input[type='range']:hover:not(:disabled)::-webkit-slider-thumb {
		transform: scale(1.12);
	}
	input[type='range']:hover:not(:disabled)::-moz-range-thumb {
		transform: scale(1.12);
	}
	input[type='range']:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 3px;
		border-radius: 999px;
	}

	.readout {
		flex: 0 0 auto;
		width: 82px;
		border: 1px solid var(--line2);
		border-radius: 999px;
		background: var(--panel2);
		color: var(--text);
		font: 12px/1.4 var(--font-mono);
		text-align: center;
		padding: 6px 8px;
	}
	.readout:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	.readout:disabled {
		opacity: 0.58;
		cursor: not-allowed;
	}

	@media (prefers-reduced-motion: reduce) {
		input[type='range']::-webkit-slider-thumb,
		input[type='range']::-moz-range-thumb {
			transition: none;
		}
	}
</style>
