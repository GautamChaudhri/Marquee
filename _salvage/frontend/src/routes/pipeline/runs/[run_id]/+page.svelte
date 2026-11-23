<script lang="ts">
	import RunResultsView from '$lib/components/pipeline/RunResultsView.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<!-- Keyed on the run so navigating between two runs remounts the view; it holds the
	 run, the inspected poster, and the active stage tab in local state. -->
{#key data.runId}
	<RunResultsView {data} />
{/key}
