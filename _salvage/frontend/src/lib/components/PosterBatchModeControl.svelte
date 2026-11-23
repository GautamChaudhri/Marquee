<script lang="ts">
	import Icon from './Icon.svelte';
	import type { PosterBatchMode } from '$lib/api/types';

	const MIN_CHUNK_SIZE = 1;
	const MAX_CHUNK_SIZE = 16;

	let {
		mode = $bindable('chunked'),
		chunkSize = $bindable(8),
		disabled = false
	}: {
		mode?: PosterBatchMode;
		chunkSize?: number;
		disabled?: boolean;
	} = $props();

	function normalizeChunkSize() {
		chunkSize = Math.min(
			MAX_CHUNK_SIZE,
			Math.max(MIN_CHUNK_SIZE, Math.round(Number(chunkSize) || 8))
		);
	}

	function adjustChunkSize(delta: number) {
		normalizeChunkSize();
		chunkSize = Math.min(MAX_CHUNK_SIZE, Math.max(MIN_CHUNK_SIZE, chunkSize + delta));
	}
</script>

<div class="batch-control" aria-label="Poster batch processing">
	<span class="control-label">Batching</span>
	<div class="mode-row">
		<div class="mode-switch" role="group" aria-label="Poster batch mode">
			<button
				type="button"
				class:active={mode === 'all_at_once'}
				aria-pressed={mode === 'all_at_once'}
				{disabled}
				onclick={() => (mode = 'all_at_once')}
			>
				Unified
			</button>
			<button
				type="button"
				class:active={mode === 'chunked'}
				aria-pressed={mode === 'chunked'}
				{disabled}
				onclick={() => (mode = 'chunked')}
			>
				Chunks
			</button>
		</div>

		{#if mode === 'chunked'}
			<div class="chunk-size">
				<label for="poster-batch-chunk-size">Chunk size</label>
				<span class="chunk-stepper">
					<input
						id="poster-batch-chunk-size"
						type="number"
						min={MIN_CHUNK_SIZE}
						max={MAX_CHUNK_SIZE}
						step="1"
						bind:value={chunkSize}
						{disabled}
						onblur={normalizeChunkSize}
						aria-describedby="batch-mode-help"
					/>
					<span class="stepper-actions" aria-label="Adjust chunk size">
						<button
							type="button"
							onclick={() => adjustChunkSize(1)}
							disabled={disabled || Number(chunkSize) >= MAX_CHUNK_SIZE}
							aria-label="Increase chunk size"
						>
							<Icon name="chevron-up" size={12} stroke={2.4} />
						</button>
						<button
							type="button"
							onclick={() => adjustChunkSize(-1)}
							disabled={disabled || Number(chunkSize) <= MIN_CHUNK_SIZE}
							aria-label="Decrease chunk size"
						>
							<Icon name="chevron-down" size={12} stroke={2.4} />
						</button>
					</span>
				</span>
			</div>
		{/if}
	</div>

	<div id="batch-mode-help" class="mode-notes" aria-live="polite">
		{#if mode === 'chunked'}
			<span class="mode-note pros"
				><strong>Pros</strong><span>Partially cancel individual chunks.</span></span
			>
			<span class="mode-note cons"
				><strong>Cons</strong><span>Slightly longer processing times.</span></span
			>
		{:else}
			<span class="mode-note pros"
				><strong>Pros</strong><span>Fastest overall processing time.</span></span
			>
			<span class="mode-note cons"
				><strong>Cons</strong><span>Cancelling stops everything.</span></span
			>
		{/if}
	</div>
</div>

<style>
	.batch-control {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 10px;
		min-height: 38px;
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--panel) 82%, transparent);
	}
	.control-label,
	.chunk-size > label {
		font-size: 12px;
		font-weight: 600;
		color: var(--muted);
		white-space: nowrap;
	}
	.mode-row {
		display: inline-flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.mode-switch {
		display: inline-flex;
		padding: 2px;
		border: 1px solid var(--line2);
		border-radius: 999px;
		background: var(--panel2);
	}
	.mode-switch button {
		padding: 6px 11px;
		border: 0;
		border-radius: 999px;
		background: transparent;
		color: var(--muted);
		font: inherit;
		font-size: 12px;
		white-space: nowrap;
		cursor: pointer;
	}
	.mode-switch button.active {
		background: var(--gold);
		color: var(--on-gold);
		font-weight: 650;
	}
	.mode-switch button:focus-visible,
	.chunk-size input:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	.mode-switch button:disabled,
	.chunk-size input:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.chunk-size {
		display: flex;
		align-items: center;
		gap: 7px;
	}
	.chunk-stepper {
		display: inline-flex;
		align-items: stretch;
		height: 32px;
		border: 1px solid var(--line2);
		border-radius: 8px;
		overflow: hidden;
		background: var(--panel2);
	}
	.chunk-size input {
		width: 42px;
		padding: 5px 5px;
		border: 0;
		background: transparent;
		color: var(--text);
		font: inherit;
		font-size: 12px;
		font-variant-numeric: tabular-nums;
		text-align: center;
		-moz-appearance: textfield;
		appearance: textfield;
	}
	.chunk-size input::-webkit-inner-spin-button,
	.chunk-size input::-webkit-outer-spin-button {
		margin: 0;
		-webkit-appearance: none;
	}
	.stepper-actions {
		display: grid;
		grid-template-rows: repeat(2, 1fr);
		width: 24px;
		border-left: 1px solid var(--line2);
	}
	.stepper-actions button {
		display: grid;
		place-items: center;
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--muted);
		cursor: pointer;
	}
	.stepper-actions button + button {
		border-top: 1px solid var(--line2);
	}
	.stepper-actions button:hover:not(:disabled) {
		background: color-mix(in srgb, var(--gold) 12%, transparent);
		color: var(--gold);
	}
	.stepper-actions button:focus-visible {
		position: relative;
		z-index: 1;
		outline: 2px solid var(--gold);
		outline-offset: -2px;
	}
	.stepper-actions button:disabled {
		color: var(--faint);
		cursor: not-allowed;
	}
	.mode-notes {
		display: grid;
		gap: 5px;
		flex-basis: 100%;
		font-size: 11px;
		line-height: 1.35;
		color: var(--faint);
	}
	.mode-note {
		display: grid;
		grid-template-columns: 32px minmax(0, 1fr);
		gap: 7px;
	}
	.mode-note strong {
		font-size: 9px;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
	}
	.mode-note.pros strong {
		color: var(--good);
	}
	.mode-note.cons strong {
		color: var(--warn);
	}
	@media (max-width: 720px) {
		.batch-control {
			align-items: flex-start;
		}
	}
</style>
