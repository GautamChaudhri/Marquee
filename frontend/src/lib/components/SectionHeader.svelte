<script lang="ts">
	import type { Snippet } from 'svelte';
	let { title, subtitle, action }: { title: string; subtitle?: string; action?: Snippet } =
		$props();
</script>

<div class="head">
	<div>
		<h2>{title}</h2>
		{#if subtitle}<p>{subtitle}</p>{/if}
	</div>
	{#if action}
		<div class="action">{@render action()}</div>
	{/if}
</div>

<style>
	.head {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: 16px;
	}
	h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 650;
		letter-spacing: -0.01em;
	}
	p {
		margin: 3px 0 0;
		font-size: 13px;
		color: var(--muted);
	}
</style>
