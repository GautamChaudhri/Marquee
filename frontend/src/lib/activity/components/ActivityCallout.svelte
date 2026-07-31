<script lang="ts">
	import type { ActivityCalloutView } from '../presentation';

	let { callout }: { callout: ActivityCalloutView } = $props();
</script>

<aside class="callout" data-tone={callout.tone} aria-label="Activity notice">
	<span class="marker" aria-hidden="true"></span>
	<div>
		<strong>{callout.label}</strong>
		{#if callout.message}<span>{callout.message}</span>{/if}
	</div>
</aside>

<style>
	.callout {
		display: flex;
		align-items: flex-start;
		gap: 9px;
		padding: 9px 10px;
		border: 1px solid color-mix(in srgb, var(--callout-color) 40%, var(--line));
		border-radius: var(--radius-sm);
		background: color-mix(in srgb, var(--callout-color) 8%, var(--panel2));
		color: var(--text);
		font-size: 12px;
		--callout-color: var(--muted);
	}
	/* Every tone in CardTone needs a rule here. A tone with no rule falls back to the
	   muted default, which reads as "nothing to see" on a line that exists to be seen. */
	.callout[data-tone='active'] {
		--callout-color: var(--info);
	}
	.callout[data-tone='positive'] {
		--callout-color: var(--good);
	}
	.callout[data-tone='warning'] {
		--callout-color: var(--warn);
	}
	.callout[data-tone='negative'] {
		--callout-color: var(--bad);
	}
	.marker {
		width: 8px;
		height: 8px;
		margin-top: 4px;
		flex: none;
		border-radius: 50%;
		background: var(--callout-color);
	}
	.callout div {
		display: grid;
		gap: 2px;
	}
	.callout span:not(.marker) {
		color: var(--muted);
	}
</style>
