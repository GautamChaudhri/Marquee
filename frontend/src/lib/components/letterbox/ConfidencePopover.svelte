<script lang="ts">
	const LEVELS = ['high', 'medium', 'low', 'variable'] as const;

	let {
		levels,
		count,
		label,
		disabled = false,
		onToggleLevel,
		onToggleAll,
		onConfirm
	}: {
		levels: string[];
		count: number;
		label: string;
		disabled?: boolean;
		onToggleLevel: (level: string) => void;
		onToggleAll: () => void;
		onConfirm: () => void;
	} = $props();

	let open = $state(false);
	let container = $state<HTMLDivElement | undefined>();
	const isAll = $derived(levels.includes('all'));

	function isChecked(level: string): boolean {
		return isAll || levels.includes(level);
	}

	function confirm() {
		open = false;
		onConfirm();
	}

	function handleWindowClick(e: MouseEvent) {
		if (open && container && !container.contains(e.target as Node)) {
			open = false;
		}
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div class="confidence-popover" bind:this={container}>
	<div class="popover-buttons">
		<button
			class="btn btn-outline btn-sm"
			type="button"
			disabled={disabled || count === 0}
			onclick={confirm}
		>
			{label} ({count})
		</button>
		<button
			class="btn btn-outline btn-sm popover-caret"
			type="button"
			aria-label="Choose confidence levels"
			aria-expanded={open}
			onclick={() => (open = !open)}
		>
			▾
		</button>
	</div>
	{#if open}
		<div class="popover-panel">
			<label class="checkbox-container">
				<input type="checkbox" checked={isAll} onchange={onToggleAll} />
				<span class="checkmark"></span>
				All
			</label>
			{#each LEVELS as level (level)}
				<label class="checkbox-container">
					<input type="checkbox" checked={isChecked(level)} onchange={() => onToggleLevel(level)} />
					<span class="checkmark"></span>
					{level.charAt(0).toUpperCase()}{level.slice(1)}
				</label>
			{/each}
		</div>
	{/if}
</div>

<style>
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 6px 12px;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		background: var(--ink3);
		color: var(--text);
		font-size: 12px;
		font-weight: 600;
		cursor: pointer;
		transition: all 0.15s ease;
	}
	.btn:hover:not(:disabled) {
		filter: brightness(1.1);
	}
	.btn:disabled {
		color: var(--text);
		opacity: 0.55;
		cursor: not-allowed;
	}
	.btn-outline {
		background: transparent;
		border-color: var(--line2);
	}
	.btn-outline:hover:not(:disabled) {
		background: var(--ink2);
	}
	.btn-sm {
		padding: 4px 8px;
		font-size: 11px;
	}
	.confidence-popover {
		position: relative;
		display: inline-flex;
	}
	.popover-buttons {
		display: inline-flex;
	}
	.popover-caret {
		padding: 0 6px;
		margin-left: -1px;
	}
	.popover-panel {
		position: absolute;
		top: calc(100% + 4px);
		right: 0;
		z-index: 20;
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 8px 10px;
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: 8px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
		min-width: 110px;
	}
	.checkbox-container {
		display: inline-flex;
		align-items: center;
		position: relative;
		cursor: pointer;
		font-size: 11px;
		font-weight: 600;
		color: var(--muted);
		user-select: none;
		gap: 6px;
	}
	.checkbox-container input {
		position: absolute;
		opacity: 0;
		cursor: pointer;
		height: 0;
		width: 0;
	}
	.checkmark {
		height: 12px;
		width: 12px;
		flex: 0 0 auto;
		background-color: var(--ink2);
		border: 1px solid var(--line2);
		border-radius: 2px;
		display: inline-block;
		transition: all 0.1s ease;
	}
	.checkbox-container input:checked ~ .checkmark {
		background-color: var(--gold);
		border-color: var(--gold);
	}
</style>
