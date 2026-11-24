<script lang="ts">
	import { theme, toggleTheme } from '$lib/stores/theme';
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	let { title, actions }: { title: string; actions?: Snippet } = $props();
</script>

<header>
	<span class="title">{title}</span>
	<div class="actions">
		{#if actions}{@render actions()}{/if}
		<button class="icon" onclick={toggleTheme} title="Toggle theme" aria-label="Toggle theme">
			<Icon name={$theme === 'dark' ? 'sun' : 'moon'} size={17} />
		</button>
		<form method="POST" action="/signout">
			<button class="signout" type="submit">Sign out</button>
		</form>
	</div>
</header>

<style>
	header {
		height: var(--header-h);
		flex: none;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 0 16px;
		border-bottom: 1px solid var(--line);
		background: color-mix(in srgb, var(--ink) 80%, transparent);
		backdrop-filter: blur(8px);
		position: sticky;
		top: 0;
		z-index: 50;
	}
	.title {
		font-weight: 650;
		font-size: 15px;
		line-height: 1.2;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}
	form {
		margin: 0;
	}
	.icon,
	.signout {
		display: grid;
		place-items: center;
		height: 32px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--muted);
	}
	.icon {
		width: 32px;
	}
	.signout {
		padding: 0 10px;
		font-size: 12.5px;
		white-space: nowrap;
	}
	.icon:hover,
	.signout:hover {
		color: var(--gold);
		border-color: var(--faint);
	}
</style>
