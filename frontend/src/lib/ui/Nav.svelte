<script lang="ts" module>
	export interface NavItem {
		href: string;
		label: string;
		icon: string;
		/** Whether the item is the current page's section. */
		active: boolean;
		/** A number to show beside the label, such as how many reviews wait; none when null. */
		count?: number | null;
	}
</script>

<script lang="ts">
	import Icon from './Icon.svelte';

	let { items }: { items: NavItem[] } = $props();
</script>

<nav aria-label="Main">
	<a class="brand" href="/">Marquee</a>
	<ul>
		{#each items as item (item.href)}
			<li>
				<a
					href={item.href}
					class:on={item.active}
					aria-current={item.active ? 'page' : undefined}
					title={item.label}
				>
					<Icon name={item.icon} size={17} />
					<span class="label">{item.label}</span>
					{#if item.count}<span class="count" aria-label="{item.count} waiting">{item.count}</span
						>{/if}
				</a>
			</li>
		{/each}
	</ul>
</nav>

<style>
	nav {
		display: flex;
		flex-direction: column;
		gap: 18px;
		padding: 16px 12px;
	}
	.brand {
		font-weight: 750;
		font-size: 17px;
		letter-spacing: -0.01em;
		color: var(--gold-copy);
		padding: 0 8px;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	ul a {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 10px;
		border-radius: var(--radius-sm);
		color: var(--muted);
	}
	ul a:hover {
		color: var(--text);
		background: var(--panel2);
	}
	ul a.on {
		color: var(--text);
		background: var(--gold-soft);
	}
	.count {
		margin-left: auto;
		min-width: 20px;
		padding: 0 6px;
		border-radius: var(--radius-pill);
		background: var(--gold);
		color: var(--on-gold);
		font-size: 11px;
		font-weight: 700;
		text-align: center;
		font-variant-numeric: tabular-nums;
	}
	@media (max-width: 720px) {
		nav {
			flex-direction: row;
			align-items: center;
			gap: 12px;
			padding: 8px 12px;
		}
		ul {
			flex-direction: row;
			gap: 2px;
		}
		ul a {
			padding: 6px 8px;
			gap: 4px;
		}
		/* Icons only on a narrow screen; the label stays for screen readers. */
		.label {
			position: absolute;
			width: 1px;
			height: 1px;
			overflow: hidden;
			clip-path: inset(50%);
			white-space: nowrap;
		}
		.count {
			min-width: 16px;
			padding: 0 4px;
			font-size: 10px;
		}
	}
</style>
