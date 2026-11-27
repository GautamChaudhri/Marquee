<script lang="ts" module>
	// The modals open now, the newest last: Escape closes only that one.
	const stack: symbol[] = [];
</script>

<script lang="ts">
	import { onMount, type Snippet } from 'svelte';

	/** A popup over the page: a dimmed backdrop and one panel. Escape, or a click on the
	 *  backdrop, closes it; focus moves into it and goes back where it was when it closes.
	 *  While any popup is open the page behind does not scroll (`mq-modal-open` in app.css).
	 *  `bare` drops the panel's own surface, for a picture shown on the backdrop itself. */
	let {
		label,
		onClose,
		maxWidth = '920px',
		bare = false,
		testid,
		children
	}: {
		label: string;
		onClose: () => void;
		maxWidth?: string;
		bare?: boolean;
		testid?: string;
		children: Snippet;
	} = $props();

	const me = Symbol('modal');
	let panel = $state<HTMLDivElement>();

	onMount(() => {
		const before = document.activeElement;
		stack.push(me);
		document.documentElement.classList.add('mq-modal-open');
		panel?.focus();
		return () => {
			stack.splice(stack.indexOf(me), 1);
			if (stack.length === 0) document.documentElement.classList.remove('mq-modal-open');
			if (before instanceof HTMLElement && before.isConnected) before.focus();
		};
	});

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape' && stack[stack.length - 1] === me) {
			e.preventDefault();
			onClose();
		}
	}
</script>

<svelte:window onkeydown={onKey} />

<div
	class="backdrop"
	class:bare
	role="button"
	tabindex="-1"
	aria-label="Dismiss"
	onclick={(e) => e.target === e.currentTarget && onClose()}
	onkeydown={(e) => e.target === e.currentTarget && e.key === 'Enter' && onClose()}
>
	<div
		bind:this={panel}
		class="panel mq-rise"
		class:bare
		style:--max-w={maxWidth}
		role="dialog"
		tabindex="-1"
		aria-modal="true"
		aria-label={label}
		data-testid={testid}
	>
		{@render children()}
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 150;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 16px;
		background: color-mix(in srgb, var(--ink) 62%, transparent);
		backdrop-filter: blur(4px);
		cursor: default;
		overscroll-behavior: contain;
	}
	.backdrop.bare {
		z-index: 160;
		padding: 0;
		background: color-mix(in srgb, var(--ink) 90%, transparent);
	}
	.panel {
		width: 100%;
		max-width: var(--max-w);
		max-height: calc(100dvh - 32px);
		display: flex;
		flex-direction: column;
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		box-shadow: 0 24px 60px var(--shadow);
		outline: none;
	}
	.panel.bare {
		max-width: none;
		height: 100dvh;
		max-height: none;
		background: none;
		border: 0;
		border-radius: 0;
		box-shadow: none;
	}
</style>
