<script lang="ts">
	import { dismiss, toasts } from '#lib/stores/toast.ts';
</script>

<div class="toasts" aria-live="polite" role="status">
	{#each $toasts as item (item.id)}
		<div class="toast mq-rise {item.tone}">
			<span>{item.message}</span>
			<button onclick={() => dismiss(item.id)} aria-label="Dismiss">×</button>
		</div>
	{/each}
</div>

<style>
	.toasts {
		position: fixed;
		right: 16px;
		bottom: 16px;
		left: 16px;
		z-index: 300;
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 8px;
		pointer-events: none;
	}
	.toast {
		pointer-events: auto;
		max-width: 420px;
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 10px 12px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: var(--panel);
		box-shadow: 0 12px 30px var(--shadow);
		font-size: 13px;
	}
	.toast.good {
		border-color: color-mix(in srgb, var(--good) 55%, transparent);
	}
	.toast.bad {
		border-color: color-mix(in srgb, var(--bad) 55%, transparent);
		color: var(--bad);
	}
	button {
		border: 0;
		background: none;
		color: var(--muted);
		font-size: 16px;
		line-height: 1;
		padding: 0 2px;
	}
</style>
