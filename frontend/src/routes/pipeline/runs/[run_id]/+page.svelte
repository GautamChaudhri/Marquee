<script lang="ts">
	import RunResultsView from '$lib/components/pipeline/RunResultsView.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<RunResultsView {data} />
