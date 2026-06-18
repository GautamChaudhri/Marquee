<script lang="ts">
	import { toasts, dismiss } from '$lib/toast';
	import { toneVar } from '$lib/display';
</script>

<div class="wrap" aria-live="polite">
	{#each $toasts as t (t.id)}
		<button class="toast" style="--c:{toneVar(t.tone)}" onclick={() => dismiss(t.id)}>
			<span class="dot"></span>{t.message}
		</button>
	{/each}
</div>

<style>
	.wrap {
		position: fixed;
		bottom: 20px;
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		flex-direction: column;
		gap: 8px;
		z-index: 200;
	}
	.toast {
		display: flex;
		align-items: center;
		gap: 9px;
		padding: 10px 16px;
		border-radius: 10px;
		background: var(--panel2);
		border: 1px solid var(--line2);
		color: var(--text);
		font-size: 13px;
		box-shadow: 0 8px 30px var(--shadow);
		animation: mq-rise 0.2s ease;
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--c);
		flex: none;
	}
</style>
