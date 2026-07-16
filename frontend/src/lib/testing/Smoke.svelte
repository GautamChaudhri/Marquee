<!--
	Minimal Svelte 5 (runes) fixture used only to prove the component test harness
	renders into jsdom and Testing Library queries + jest-dom matchers work. Not
	imported by application code.
-->
<script lang="ts">
	let { label = 'smoke' }: { label?: string } = $props();
</script>

<p data-testid="smoke">{label}</p>
