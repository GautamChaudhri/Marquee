<script lang="ts">
	import { toneVar, type Tone } from '$lib/display';
	let {
		value = 0,
		tone = 'gold',
		height = 6
	}: { value?: number; tone?: Tone; height?: number } = $props();
	const pct = $derived(Math.max(0, Math.min(100, value)));
</script>

<div class="track" style="height:{height}px">
	<div class="fill" style="width:{pct}%; --c:{toneVar(tone)}"></div>
</div>

<style>
	.track {
		width: 100%;
		background: var(--line);
		border-radius: 99px;
		overflow: hidden;
	}
	.fill {
		height: 100%;
		background: var(--c);
		border-radius: 99px;
		transition: width 0.3s ease;
	}
</style>
