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

	// A settled poster group: "TVP" tile, the batch and group ordinals under the title,
	// the stage on the bar, and the outcome stated by pills instead of a callout.
	const reviewRow = makeRow({
		job_id: 'poster-group-1',
		job_type: 'poster_pipeline_group',
		subject: subjects.posterGroup,
		action_headline: 'Select posters across the TV library',
		status: {
			label: 'Partially succeeded',
			label_key: 'jobs.status.partially_succeeded',
			phase: 'terminal',
			outcome: 'partially_succeeded',
			tone: 'warning'
		},
		attention: {
			level: 'warning',
			reason: 'review',
			message: '1 poster needs attention.',
			remediation: null
		}
	});
	const reviewWorkItems = {
		version: 1 as const,
		total: 8,
		counts: {
			pending: 0,
			running: 0,
			succeeded: 7,
			no_change: 0,
			review_required: 1,
			failed: 0,
			cancelled: 0
		},
		sequence: 9,
		updated_at: '2026-07-16T12:05:00Z',
		href: '/api/jobs/poster-group-1/work-items'
	};
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
	<section aria-label="Settled poster group fixture">
		<h2>Poster group with an open decision</h2>
		<JobProgressCard row={reviewRow} workItems={reviewWorkItems} />
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
