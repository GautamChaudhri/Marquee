<script lang="ts">
	import { toneVar, type Tone } from '$lib/display';
	let {
		tone = 'muted',
		size = 8,
		title
	}: { tone?: Tone; size?: number; title?: string } = $props();
</script>

<span class="dot" title={title ?? tone} style="--c:{toneVar(tone)}; width:{size}px; height:{size}px"
></span>

<style>
	.dot {
		display: inline-block;
		border-radius: 50%;
		background: var(--c);
		flex: none;
		box-shadow: 0 0 0 3px color-mix(in srgb, var(--c) 16%, transparent);
	}
</style>
