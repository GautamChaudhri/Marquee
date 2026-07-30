<script lang="ts">
	import type { PosterBatchMode } from '$lib/api/types';

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
		chunkSize = Math.min(16, Math.max(1, Math.round(Number(chunkSize) || 8)));
	}
</script>

<div class="batch-control" aria-label="Poster batch processing">
	<span class="control-label">Batching</span>
	<div class="mode-switch" role="group" aria-label="Poster batch mode">
		<button
			type="button"
			class:active={mode === 'chunked'}
			aria-pressed={mode === 'chunked'}
			{disabled}
			onclick={() => (mode = 'chunked')}
		>
			Chunks
		</button>
		<button
			type="button"
			class:active={mode === 'all_at_once'}
			aria-pressed={mode === 'all_at_once'}
			{disabled}
			onclick={() => (mode = 'all_at_once')}
		>
			All at once
		</button>
	</div>

	{#if mode === 'chunked'}
		<label class="chunk-size">
			<span>Chunk size</span>
			<input
				type="number"
				min="1"
				max="16"
				step="1"
				bind:value={chunkSize}
				{disabled}
				onblur={normalizeChunkSize}
				aria-describedby="batch-mode-help"
			/>
		</label>
		<span id="batch-mode-help" class="help">Each completed chunk becomes reviewable.</span>
	{:else}
		<span id="batch-mode-help" class="help">Faster startup; cancelling stops the whole batch.</span>
	{/if}
</div>

<style>
	.batch-control {
		display: flex;
		align-items: center;
		gap: 10px;
		min-height: 38px;
		padding: 8px 10px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--panel) 82%, transparent);
	}
	.control-label,
	.chunk-size span {
		font-size: 12px;
		font-weight: 600;
		color: var(--muted);
		white-space: nowrap;
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
	.chunk-size input {
		width: 58px;
		padding: 6px 7px;
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel2);
		color: var(--text);
		font: inherit;
		font-size: 12px;
		font-variant-numeric: tabular-nums;
	}
	.help {
		font-size: 11px;
		line-height: 1.35;
		color: var(--faint);
	}
	@media (max-width: 720px) {
		.batch-control {
			align-items: flex-start;
			flex-wrap: wrap;
		}
		.help {
			flex-basis: 100%;
		}
	}
</style>
