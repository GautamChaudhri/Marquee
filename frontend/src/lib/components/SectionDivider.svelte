<script lang="ts">
	import type { Snippet } from 'svelte';

	/** A labelled hairline that splits a long page into tiers. Purely structural —
	 *  it groups the cards below it without adding another bordered box. */
	let { label, note, action }: { label: string; note?: string; action?: Snippet } = $props();
</script>

<div class="divider">
	<h2>{label}</h2>
	{#if note}<span class="note">{note}</span>{/if}
	<span class="rule"></span>
	{#if action}<div class="action">{@render action()}</div>{/if}
</div>

<style>
	.divider {
		display: flex;
		align-items: center;
		gap: 10px;
		margin: 26px 0 12px;
	}
	/* The top margin separates tiers; leading the page with it is just dead space. */
	.divider:first-child {
		margin-top: 0;
	}
	h2 {
		margin: 0;
		flex: none;
		color: var(--muted);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.09em;
		text-transform: uppercase;
	}
	.note {
		flex: none;
		color: var(--faint);
		font-size: 11.5px;
	}
	.rule {
		flex: 1;
		height: 1px;
		min-width: 12px;
		background: var(--line);
	}
	.action {
		flex: none;
		display: flex;
		align-items: center;
		gap: 8px;
	}
</style>
