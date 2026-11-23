<script lang="ts">
	interface Tab {
		id: string;
		label: string;
		count?: number;
	}
	let { tabs, active, onSelect }: { tabs: Tab[]; active: string; onSelect: (id: string) => void } =
		$props();
</script>

<div class="tabs" role="tablist">
	{#each tabs as t (t.id)}
		<button
			role="tab"
			aria-selected={active === t.id}
			class="tab"
			class:on={active === t.id}
			onclick={() => onSelect(t.id)}
		>
			{t.label}
			{#if t.count != null}<span class="count">{t.count}</span>{/if}
		</button>
	{/each}
</div>

<style>
	.tabs {
		display: flex;
		gap: 4px;
		flex-wrap: wrap;
	}
	.tab {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 6px 12px;
		border-radius: 99px;
		border: 1px solid transparent;
		background: transparent;
		color: var(--muted);
		font-size: 13px;
		font-weight: 500;
		transition:
			background 0.12s,
			color 0.12s;
	}
	.tab:hover {
		color: var(--text);
		background: var(--panel2);
	}
	.tab.on {
		color: var(--on-gold);
		background: var(--gold);
		border-color: var(--gold-deep);
	}
	.count {
		font-family: var(--font-mono);
		font-size: 11px;
		padding: 0 5px;
		border-radius: 99px;
		background: color-mix(in srgb, currentColor 18%, transparent);
	}
</style>
