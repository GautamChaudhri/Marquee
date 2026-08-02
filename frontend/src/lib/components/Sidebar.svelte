<script lang="ts">
	import { page } from '$app/state';
	import { sidebarCollapsed } from '$lib/theme';
	import ActivityNavBadge from '$lib/activity/components/ActivityNavBadge.svelte';
	import Icon from './Icon.svelte';

	interface SubLink {
		label: string;
		href: string;
		icon: string;
	}
	interface Link {
		label: string;
		href: string;
		icon: string;
		activityBadge?: boolean;
		children?: SubLink[];
	}
	const GROUPS: { name: string; links: Link[] }[] = [
		{
			name: 'Library',
			links: [
				{ label: 'Dashboard', href: '/dashboard', icon: 'dashboard' },
				{ label: 'Films', href: '/films', icon: 'film' },
				{ label: 'Television', href: '/television', icon: 'tv' }
			]
		},
		{
			name: 'Posters',
			links: [
				{
					label: 'Pipeline',
					href: '/pipeline',
					icon: 'pipeline',
					children: [
						{ label: 'Movies', href: '/pipeline/movies', icon: 'film' },
						{ label: 'TV', href: '/pipeline/tv', icon: 'tv' }
					]
				},
				{ label: 'Taste', href: '/taste', icon: 'taste' }
			]
		},
		{
			name: 'System',
			links: [
				{
					label: 'Activity',
					href: '/projection-room',
					icon: 'projection-room',
					activityBadge: true
				},
				{ label: 'Settings', href: '/settings', icon: 'settings' }
			]
		}
	];

	const isActive = (href: string) =>
		page.url.pathname === href || page.url.pathname.startsWith(href + '/');
	const isExact = (href: string) => page.url.pathname === href;
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
					{#if l.children}
						<div class="split-row">
							<a
								href={l.href}
								class="link split-main"
								class:on={isExact(l.href)}
								class:section-on={isActive(l.href) && !isExact(l.href)}
								title={l.label}
								aria-label={l.label}
								aria-current={isExact(l.href) ? 'page' : undefined}
							>
								<Icon name={l.icon} size={18} />
								{#if !$sidebarCollapsed}<span>{l.label}</span>{/if}
							</a>
							<div class="workspace-links">
								{#each l.children as child (child.href)}
									<a
										href={child.href}
										class="workspace-link"
										class:on={isActive(child.href)}
										title={`${l.label} · ${child.label}`}
										aria-label={`${l.label}: ${child.label}`}
										aria-current={isActive(child.href) ? 'page' : undefined}
									>
										<Icon name={child.icon} size={16} />
									</a>
								{/each}
							</div>
						</div>
					{:else}
						<a href={l.href} class="link" class:on={isActive(l.href)} title={l.label}>
							<Icon name={l.icon} size={18} />
							{#if !$sidebarCollapsed}<span>{l.label}</span>{/if}
							{#if l.activityBadge}<ActivityNavBadge />{/if}
						</a>
					{/if}
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
		color: var(--muted);
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
	:global([data-theme='light']) .link.on {
		color: color-mix(in srgb, var(--gold) 50%, var(--text));
	}
	.link:focus-visible,
	.workspace-link:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	/* Pipeline and its two media workspaces read as one compact navigation control. */
	.split-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: stretch;
		gap: 4px;
	}
	.split-main {
		min-width: 0;
	}
	.split-main.section-on {
		background: color-mix(in srgb, var(--gold) 6%, var(--panel));
		color: var(--text);
	}
	.workspace-links {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 2px;
		border: 1px solid var(--line);
		border-radius: 8px;
		background: var(--panel);
	}
	.workspace-link {
		display: flex;
		width: 30px;
		min-height: 30px;
		align-items: center;
		justify-content: center;
		border-radius: 6px;
		color: var(--muted);
	}
	.workspace-link:hover {
		background: var(--panel2);
		color: var(--text);
	}
	.workspace-link.on {
		background: var(--gold);
		color: var(--on-gold);
	}
	.collapsed .split-row {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.collapsed .workspace-links {
		flex-direction: column;
	}
	.collapsed .workspace-link {
		width: 30px;
		min-height: 28px;
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
		color: var(--muted);
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
