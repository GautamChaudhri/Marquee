<script lang="ts">
	import SectionHeader from './SectionHeader.svelte';
	let { title, subtitle, note }: { title: string; subtitle?: string; note?: string } = $props();
</script>

<SectionHeader {title} {subtitle} />
<div class="stub">
	<p>{note ?? 'This screen is part of an upcoming vertical slice.'}</p>
</div>

<style>
	.stub {
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		padding: 48px 24px;
		text-align: center;
		color: var(--faint);
		background: var(--panel);
		font-size: 13.5px;
	}
	p {
		margin: 0;
	}
</style>
