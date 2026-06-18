<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { page } from '$app/state';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import TopBar from '$lib/components/TopBar.svelte';
	import Toast from '$lib/components/Toast.svelte';
	import CmdPalette from '$lib/components/CmdPalette.svelte';

	let { children } = $props();
	let paletteOpen = $state(false);

	function onKeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			paletteOpen = !paletteOpen;
		}
	}
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>
<svelte:window onkeydown={onKeydown} />

<div class="shell">
	<Sidebar />
	<main>
		<TopBar onOpenPalette={() => (paletteOpen = true)} />
		{#key page.url.pathname}
			<div class="content mq-rise">{@render children()}</div>
		{/key}
	</main>
</div>

<CmdPalette open={paletteOpen} onClose={() => (paletteOpen = false)} />
<Toast />

<style>
	.shell {
		display: flex;
		min-height: 100vh;
	}
	main {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
	}
	.content {
		padding: 24px;
		width: 100%;
		max-width: 1440px;
	}
</style>
