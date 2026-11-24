<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	let {
		variant = 'secondary',
		busy = false,
		disabled = false,
		type = 'button',
		children,
		...rest
	}: HTMLButtonAttributes & {
		variant?: 'primary' | 'secondary' | 'danger';
		busy?: boolean;
		children: Snippet;
	} = $props();
</script>

<button
	{...rest}
	{type}
	class="btn {variant}"
	disabled={disabled || busy}
	aria-busy={busy || undefined}
>
	{#if busy}<span class="spin" aria-hidden="true">⟳</span>{/if}
	{@render children()}
</button>

<style>
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		min-height: 32px;
		padding: 0 12px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		font-size: 13px;
		white-space: nowrap;
	}
	.btn:hover:not(:disabled) {
		border-color: var(--faint);
	}
	.primary {
		border-color: var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-weight: 600;
	}
	.danger {
		border-color: color-mix(in srgb, var(--bad) 55%, transparent);
		background: color-mix(in srgb, var(--bad) 12%, var(--panel));
		color: var(--bad);
	}
	.btn:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
</style>
