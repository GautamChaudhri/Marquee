<script lang="ts" module>
	export interface NavItem {
		href: string;
		label: string;
		icon: string;
		/** Whether the item is the current page's section. */
		active: boolean;
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
				<a href={item.href} class:on={item.active} aria-current={item.active ? 'page' : undefined}>
					<Icon name={item.icon} size={17} />
					<span>{item.label}</span>
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
	@media (max-width: 720px) {
		nav {
			flex-direction: row;
			align-items: center;
			gap: 12px;
			padding: 8px 12px;
		}
		ul {
			flex-direction: row;
		}
		ul a {
			padding: 6px 10px;
		}
	}
</style>
