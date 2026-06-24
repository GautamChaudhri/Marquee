<script lang="ts">
	import { page } from '$app/state';
	import { sidebarCollapsed } from '$lib/theme';
	import Icon from './Icon.svelte';

	interface Link {
		label: string;
		href: string;
		icon: string;
	}
	const GROUPS: { name: string; links: Link[] }[] = [
		{
			name: 'Library',
			links: [
				{ label: 'Dashboard', href: '/dashboard', icon: 'dashboard' },
				{ label: 'Films', href: '/films', icon: 'film' },
				{ label: 'Shows', href: '/shows', icon: 'tv' }
			]
		},
		{
			name: 'Posters',
			links: [
				{ label: 'Pipeline', href: '/pipeline', icon: 'pipeline' },
				{ label: 'Taste', href: '/taste', icon: 'taste' }
			]
		},
		{
			name: 'Toolbox',
			links: [
				{ label: 'Radarr Overlay', href: '/hdr', icon: 'hdr' },
				{ label: 'Subtitles', href: '/subtitles', icon: 'subtitles' },
				{ label: 'Letterbox', href: '/letterbox', icon: 'letterbox' }
			]
		},
		{
			name: 'System',
			links: [
				{ label: 'Activity', href: '/activity', icon: 'activity' },
				{ label: 'Settings', href: '/settings', icon: 'settings' }
			]
		}
	];

	const isActive = (href: string) =>
		page.url.pathname === href || page.url.pathname.startsWith(href + '/');
</script>

<aside class:collapsed={$sidebarCollapsed}>
	<div class="brand">
		<div class="dots" aria-hidden="true">
			{#each [0, 1, 2, 3] as i (i)}<span style="animation-delay:{i * 0.3}s"></span>{/each}
		</div>
		{#if !$sidebarCollapsed}<span class="word">Marquee</span>{/if}
	</div>

	<nav>
		{#each GROUPS as g (g.name)}
			<div class="group">
				{#if !$sidebarCollapsed}<div class="gname">{g.name}</div>{/if}
				{#each g.links as l (l.href)}
					<a href={l.href} class="link" class:on={isActive(l.href)} title={l.label}>
						<Icon name={l.icon} size={18} />
						{#if !$sidebarCollapsed}<span>{l.label}</span>{/if}
					</a>
				{/each}
			</div>
		{/each}
	</nav>

	<div class="foot">
		<a href="/taste" class="taste" title="Taste profile">
			<span class="ring"></span>
			{#if !$sidebarCollapsed}<span class="t">Taste profile<small>~430 exemplars</small></span>{/if}
		</a>
		<button
			class="collapse"
			onclick={() => sidebarCollapsed.update((v) => !v)}
			title="Toggle sidebar"
		>
			<Icon name="collapse" size={18} />
		</button>
	</div>
</aside>

<style>
	aside {
		width: var(--sidebar-w);
		flex: none;
		background: var(--ink2);
		border-right: 1px solid var(--line);
		display: flex;
		flex-direction: column;
		height: 100vh;
		position: sticky;
		top: 0;
		transition: width 0.18s ease;
	}
	aside.collapsed {
		width: var(--sidebar-w-collapsed);
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 10px;
		height: var(--header-h);
		padding: 0 16px;
		border-bottom: 1px solid var(--line);
	}
	.dots {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 3px;
		flex: none;
	}
	.dots span {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--gold);
		animation: mq-pulse 2.4s infinite;
	}
	.word {
		font-weight: 700;
		letter-spacing: -0.01em;
		font-size: 15px;
	}
	nav {
		flex: 1;
		overflow-y: auto;
		padding: 10px 8px;
	}
	.group {
		margin-bottom: 14px;
	}
	.gname {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--faint2);
		padding: 4px 10px;
		font-weight: 700;
	}
	.link {
		display: flex;
		align-items: center;
		gap: 11px;
		padding: 8px 10px;
		border-radius: 8px;
		color: var(--muted);
		font-size: 13.5px;
		font-weight: 500;
		white-space: nowrap;
	}
	.link:hover {
		background: var(--panel);
		color: var(--text);
	}
	.link.on {
		background: var(--gold-soft);
		color: var(--gold);
	}
	.collapsed .link,
	.collapsed .brand {
		justify-content: center;
	}
	.foot {
		border-top: 1px solid var(--line);
		padding: 10px 8px;
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.taste {
		flex: 1;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 8px;
		border-radius: 8px;
		color: var(--muted);
		overflow: hidden;
	}
	.taste:hover {
		background: var(--panel);
	}
	.ring {
		width: 16px;
		height: 16px;
		border-radius: 50%;
		flex: none;
		background: conic-gradient(var(--gold), var(--dovi), var(--info), var(--gold));
	}
	.t {
		display: flex;
		flex-direction: column;
		font-size: 12px;
		line-height: 1.25;
	}
	.t small {
		color: var(--faint);
		font-size: 10px;
	}
	.collapse {
		background: transparent;
		border: none;
		color: var(--muted);
		padding: 6px;
		border-radius: 7px;
		flex: none;
	}
	.collapse:hover {
		background: var(--panel);
		color: var(--text);
	}
</style>
