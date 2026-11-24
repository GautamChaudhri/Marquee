<script lang="ts">
	import '../app.css';
	import '$lib/ui/controls.css';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import WorkerBadge from '$lib/features/jobs/WorkerBadge.svelte';
	import { pendingReviews, refreshPendingReviews } from '$lib/stores/review';
	import Nav from '$lib/ui/Nav.svelte';
	import Toasts from '$lib/ui/Toasts.svelte';
	import TopBar from '$lib/ui/TopBar.svelte';
	import type { Snippet } from 'svelte';

	let { children }: { children: Snippet } = $props();

	const TITLES: Record<string, string> = {
		'/': 'Library',
		'/movies/[id]': 'Movie',
		'/review': 'Review',
		'/review/[runId]': 'Review',
		'/taste': 'Taste',
		'/activity': 'Activity',
		'/system': 'System'
	};

	const bare = $derived(page.route.id === '/signin' || page.route.id === null);
	const route = $derived<string>(page.route.id ?? '');
	const nav = $derived([
		{
			href: '/',
			label: 'Library',
			icon: 'film',
			active: route === '/' || route.startsWith('/movies')
		},
		{
			href: '/review',
			label: 'Review',
			icon: 'eye',
			active: route.startsWith('/review'),
			count: $pendingReviews || null
		},
		{ href: '/activity', label: 'Activity', icon: 'activity', active: route === '/activity' }
	]);

	// The Review entry's number: read when the shell loads, and after each answer.
	onMount(() => {
		if (!bare) void refreshPendingReviews();
	});
</script>

{#if bare}
	{@render children()}
{:else}
	<div class="app">
		<aside><Nav items={nav} /></aside>
		<div class="main">
			<TopBar title={TITLES[route] ?? 'Marquee'}>
				{#snippet actions()}<WorkerBadge />{/snippet}
			</TopBar>
			<main>{@render children()}</main>
		</div>
	</div>
{/if}
<Toasts />

<style>
	.app {
		min-height: 100vh;
		display: grid;
		grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
	}
	aside {
		border-right: 1px solid var(--line);
		background: var(--ink2);
		position: sticky;
		top: 0;
		height: 100vh;
	}
	.main {
		min-width: 0;
		display: flex;
		flex-direction: column;
	}
	main {
		padding: 20px 24px 48px;
		min-width: 0;
	}
	@media (max-width: 720px) {
		.app {
			grid-template-columns: minmax(0, 1fr);
			grid-template-rows: auto 1fr;
		}
		aside {
			position: static;
			height: auto;
			border-right: 0;
			border-bottom: 1px solid var(--line);
		}
		main {
			padding: 14px 12px 40px;
		}
	}
</style>
