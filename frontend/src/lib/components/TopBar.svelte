<script lang="ts">
	import { page } from '$app/state';
	import { theme, toggleTheme } from '$lib/theme';
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	let { onOpenPalette, action }: { onOpenPalette: () => void; action?: Snippet } = $props();

	const TITLES: Record<string, { title: string; sub: string }> = {
		dashboard: { title: 'Dashboard', sub: 'Library health & system status' },
		films: { title: 'Films', sub: 'Movie library' },
		shows: { title: 'Shows', sub: 'Series library' },
		pipeline: { title: 'Poster pipeline', sub: 'Run, review & tune selection' },
		taste: { title: 'Key Art Engine', sub: 'Taste profile & learned ranker' },
		hdr: { title: 'Radarr Overlay', sub: 'HDR targets, scores & upgrade signals' },
		subtitles: { title: 'Subtitles', sub: 'Inventory, policies & generation' },
		letterbox: { title: 'Letterbox', sub: 'Black-bar detection & cropping' },
		activity: { title: 'Activity', sub: 'Recent events' },
		settings: { title: 'Settings', sub: 'Connections & preferences' }
	};
	const seg = $derived(page.url.pathname.split('/').filter(Boolean)[0] ?? 'dashboard');
	const meta = $derived(TITLES[seg] ?? { title: 'Marquee', sub: '' });
</script>

<header>
	<div class="crumb">
		<span class="title">{meta.title}</span>
		{#if meta.sub}<span class="sub">{meta.sub}</span>{/if}
	</div>
	<div class="actions">
		{#if action}{@render action()}{/if}
		<button class="search" onclick={onOpenPalette}>
			<Icon name="search" size={15} /><span>Search</span><kbd>⌘K</kbd>
		</button>
		<button class="icon" onclick={toggleTheme} title="Toggle theme" aria-label="Toggle theme">
			<Icon name={$theme === 'dark' ? 'sun' : 'moon'} size={17} />
		</button>
	</div>
</header>

<style>
	header {
		height: var(--header-h);
		flex: none;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 0 22px;
		border-bottom: 1px solid var(--line);
		background: color-mix(in srgb, var(--ink) 80%, transparent);
		backdrop-filter: blur(8px);
		position: sticky;
		top: 0;
		z-index: 50;
	}
	.crumb {
		display: flex;
		align-items: baseline;
		gap: 10px;
	}
	.title {
		font-weight: 650;
		font-size: 15px;
	}
	.sub {
		color: var(--muted);
		font-size: 12.5px;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.search {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 6px 10px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--muted);
		font-size: 13px;
	}
	.search:hover {
		border-color: var(--faint);
		color: var(--text);
	}
	kbd {
		font-family: var(--font-mono);
		font-size: 10px;
		color: var(--faint);
		border: 1px solid var(--line2);
		border-radius: 4px;
		padding: 0 4px;
	}
	.icon {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--muted);
	}
	.icon:hover {
		color: var(--gold);
		border-color: var(--faint);
	}
</style>
