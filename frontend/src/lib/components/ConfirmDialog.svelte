<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		open = false,
		title,
		message,
		confirmLabel = 'Confirm',
		cancelLabel = 'Cancel',
		tone = 'gold',
		busy = false,
		confirmDisabled = false,
		maxWidth = '440px',
		onConfirm,
		onCancel,
		children
	}: {
		open?: boolean;
		title: string;
		message?: string;
		confirmLabel?: string;
		cancelLabel?: string;
		tone?: 'gold' | 'bad';
		busy?: boolean;
		confirmDisabled?: boolean;
		maxWidth?: string;
		onConfirm: () => void;
		onCancel: () => void;
		children?: Snippet;
	} = $props();

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape' && !busy) onCancel();
	}
</script>

<svelte:window onkeydown={onKey} />

{#if open}
	<div
		class="backdrop"
		role="button"
		tabindex="-1"
		aria-label="Dismiss"
		onclick={() => !busy && onCancel()}
		onkeydown={(e) => e.key === 'Enter' && !busy && onCancel()}
	>
		<div
			class="card mq-rise"
			style="--max-w:{maxWidth}"
			role="dialog"
			tabindex="-1"
			aria-modal="true"
			aria-label={title}
			onclick={(e) => e.stopPropagation()}
			onkeydown={(e) => e.stopPropagation()}
		>
			<div class="head">{title}</div>
			{#if message}<p class="msg">{message}</p>{/if}
			{#if children}<div class="body">{@render children()}</div>{/if}
			<div class="foot">
				<button class="btn-sec" onclick={onCancel} disabled={busy}>{cancelLabel}</button>
				<button
					class="btn-confirm"
					class:bad={tone === 'bad'}
					onclick={onConfirm}
					disabled={busy || confirmDisabled}
				>
					{#if busy}<span class="spin">⟳</span> Working…{:else}{confirmLabel}{/if}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 200;
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding: 14vh 16px 16px;
		background: color-mix(in srgb, var(--ink) 55%, transparent);
		backdrop-filter: blur(4px);
		cursor: default;
	}
	.card {
		width: 100%;
		max-width: var(--max-w, 440px);
		max-height: calc(100vh - 16vh - 32px);
		overflow-y: auto;
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		box-shadow: 0 24px 60px var(--shadow);
		padding: 18px 20px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.head {
		font-size: 15px;
		font-weight: 650;
		color: var(--text);
	}
	.msg {
		margin: 0;
		font-size: 13px;
		line-height: 1.5;
		color: var(--muted);
	}
	.body {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.foot {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 2px;
	}
	.btn-sec {
		padding: 8px 16px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 13px;
	}
	.btn-confirm {
		padding: 8px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.btn-confirm.bad {
		border-color: color-mix(in srgb, var(--bad) 55%, transparent);
		background: color-mix(in srgb, var(--bad) 16%, var(--panel2));
		color: var(--bad);
	}
	.btn-sec:disabled,
	.btn-confirm:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
</style>
