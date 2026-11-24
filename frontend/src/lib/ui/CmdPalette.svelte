<script lang="ts">
	import { goto } from '$app/navigation';
	import Icon from './Icon.svelte';

	let { open = false, onClose }: { open?: boolean; onClose: () => void } = $props();
	let query = $state('');

	// Foundation stub: navigation targets only. Later slices add film-title +
	// action fuzzy-search backed by the API.
	const TARGETS = [
		{ label: 'Dashboard', href: '/dashboard', icon: 'dashboard' },
		{ label: 'Films', href: '/films', icon: 'film' },
		{ label: 'Television', href: '/television', icon: 'tv' },
		{ label: 'Review queue', href: '/pipeline/movies?tab=review', icon: 'pipeline' },
		{ label: 'Taste map', href: '/taste', icon: 'taste' },
		{ label: 'Activity', href: '/projection-room', icon: 'projection-room' },
		{ label: 'Settings', href: '/settings', icon: 'settings' }
	];
	const matches = $derived(
		TARGETS.filter((t) => t.label.toLowerCase().includes(query.toLowerCase()))
	);

	function pick(href: string) {
		onClose();
		query = '';
		goto(href);
	}
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
		else if (e.key === 'Enter' && matches[0]) pick(matches[0].href);
	}
</script>

{#if open}
	<div
		class="backdrop"
		role="button"
		tabindex="-1"
		aria-label="Close"
		onclick={onClose}
		onkeydown={(e) => e.key === 'Enter' && onClose()}
	></div>
	<div class="palette mq-rise" role="dialog" aria-modal="true">
		<div class="search">
			<Icon name="search" size={18} />
			<!-- svelte-ignore a11y_autofocus -->
			<input
				autofocus
				placeholder="Search pages, films, actions…"
				bind:value={query}
				onkeydown={onKey}
			/>
			<kbd>esc</kbd>
		</div>
		<ul>
			{#each matches as m (m.href)}
				<li>
					<button onclick={() => pick(m.href)}><Icon name={m.icon} size={16} />{m.label}</button>
				</li>
			{:else}
				<li class="empty">No matches</li>
			{/each}
		</ul>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.45);
		backdrop-filter: blur(3px);
		z-index: 300;
	}
	.palette {
		position: fixed;
		top: 14vh;
		left: 50%;
		transform: translateX(-50%);
		width: min(560px, 92vw);
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: 14px;
		box-shadow: 0 24px 70px var(--shadow);
		z-index: 301;
		overflow: hidden;
	}
	.search {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
		color: var(--muted);
	}
	.search input {
		flex: 1;
		background: transparent;
		border: none;
		outline: none;
		color: var(--text);
		font-size: 15px;
	}
	kbd {
		font-family: var(--font-mono);
		font-size: 11px;
		color: var(--faint);
		border: 1px solid var(--line2);
		border-radius: 5px;
		padding: 1px 5px;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 6px;
		max-height: 50vh;
		overflow: auto;
	}
	li button {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 9px 10px;
		border: none;
		background: transparent;
		color: var(--text);
		border-radius: 8px;
		font-size: 14px;
		text-align: left;
	}
	li button:hover {
		background: var(--panel2);
		color: var(--gold);
	}
	.empty {
		padding: 14px;
		color: var(--faint);
		font-size: 13px;
		text-align: center;
	}
</style>
