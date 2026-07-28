<script lang="ts">
	import JobProgressCard from '$lib/activity/components/JobProgressCard.svelte';
	import {
		makePresentation,
		makeRow,
		makeSnapshot,
		subjects
	} from '$lib/activity/components/fixtures';

	const row = makeRow();
	const snapshot = makeSnapshot(row, {
		allowed_actions: ['cancel', 'open_detail', 'open_logs', 'open_artifacts']
	});
	const presentation = makePresentation(row);
	const children = [
		makeRow({ job_id: 'child-1', subject: subjects.episode }),
		makeRow({ job_id: 'child-2', subject: subjects.posterCandidates })
	];
	let cancelSent = $state(false);
	function markCancelSent(): void {
		cancelSent = true;
	}
</script>

<svelte:head><title>Activity card fixture</title></svelte:head>

<div class="fixture-page">
	<h1>Shared activity cards</h1>
	<section aria-label="Expanded card fixture">
		<h2>Expanded card</h2>
		<JobProgressCard
			{row}
			{snapshot}
			{presentation}
			{children}
			variant="expanded"
			onCancel={markCancelSent}
		/>
		{#if cancelSent}<p role="status">Cancel request sent</p>{/if}
	</section>
	<section aria-label="Compact card fixture">
		<h2>Compact card</h2>
		<JobProgressCard row={makeRow({ subject: subjects.series })} />
	</section>
</div>

<style>
	.fixture-page {
		display: grid;
		width: min(760px, 100%);
		gap: 20px;
		margin: 0 auto;
		padding: 24px;
	}
	h1,
	h2 {
		margin: 0;
	}
	h1 {
		font-size: 22px;
	}
	h2 {
		margin-bottom: 10px;
		font-size: 16px;
	}
	section {
		min-width: 0;
	}
	p {
		color: var(--good);
	}
	@media (max-width: 480px) {
		.fixture-page {
			padding: 10px;
		}
	}
</style>
