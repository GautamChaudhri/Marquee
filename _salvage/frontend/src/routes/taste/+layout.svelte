<script lang="ts">
	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';

	let { children } = $props();

	// The library each page is looking at rides in the query string, so carrying it
	// across the sub-nav keeps you in the same library when you change view.
	const library = $derived(page.url.searchParams.get('library') === 'tv' ? 'tv' : 'movies');
	const query = $derived(library === 'tv' ? '?library=tv' : '');

	const SECTIONS = [
		{ href: '/taste', label: 'Engine', icon: 'taste' },
		{ href: '/taste/map', label: 'Taste Map', icon: 'grid' },
		{ href: '/taste/history', label: 'History', icon: 'layers' }
	];
	const isCurrent = (href: string) => page.url.pathname.replace(/\/$/, '') === href;
</script>

<nav class="sections" aria-label="Key Art Engine">
	{#each SECTIONS as section (section.href)}
		<a
			href={`${section.href}${query}`}
			class:on={isCurrent(section.href)}
			aria-current={isCurrent(section.href) ? 'page' : undefined}
		>
			<Icon name={section.icon} size={15} />
			{section.label}
		</a>
	{/each}
</nav>

{@render children()}

<style>
	.sections {
		display: inline-flex;
		gap: 2px;
		padding: 4px;
		margin-bottom: 20px;
		border: 1px solid var(--line);
		border-radius: var(--radius-pill);
		background: var(--panel);
	}
	.sections a {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		padding: 7px 14px;
		border-radius: var(--radius-pill);
		color: var(--muted);
		font-size: 13px;
		font-weight: 500;
		text-decoration: none;
	}
	.sections a:hover {
		color: var(--text);
		background: var(--panel2);
	}
	.sections a.on {
		background: var(--gold);
		color: var(--on-gold);
	}
	.sections a:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
</style>
