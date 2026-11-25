<script lang="ts">
	import { page } from '$app/state';
	import { onDestroy } from 'svelte';
	import { api, call } from '#lib/api/client.ts';
	import type { Deployment, Job, Movie, RunDetail } from '#lib/api/types.ts';
	import { describeJob, followJob, isFinal } from '#lib/features/jobs/jobs.ts';
	import PosterHistory from '#lib/features/library/PosterHistory.svelte';
	import { originLabel, thumbnailUrl } from '#lib/features/library/poster.ts';
	import CandidateGrid from '#lib/features/runs/CandidateGrid.svelte';
	import { blindLabel, restoreTarget, runSummary } from '#lib/features/runs/runs.ts';
	import { formatBytes, formatDuration, formatWhen } from '#lib/format.ts';
	import { toast } from '#lib/stores/toast.ts';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import Notice from '#lib/ui/Notice.svelte';

	const movieId = $derived(Number(page.params.id));

	let movie = $state<Movie | null>(null);
	let movieError = $state<string | null>(null);
	let run = $state<RunDetail | null>(null);
	let runError = $state<string | null>(null);
	let runLoading = $state(false);
	let history = $state<Deployment[] | null>(null);
	let historyError = $state<string | null>(null);
	let posterBroken = $state(false);

	async function loadMovie(id: number) {
		movieError = null;
		const result = await call(
			api.GET('/api/movies/{movie_id}', { params: { path: { movie_id: id } } })
		);
		if (id !== movieId) return;
		if (!result.ok) {
			movieError = result.status === 404 ? 'There is no such movie.' : result.message;
			return;
		}
		movie = result.data;
		posterBroken = false;
		await loadRun(result.data.latest_run_id);
	}

	async function loadRun(runId: number | null) {
		runError = null;
		if (runId === null) {
			run = null;
			return;
		}
		runLoading = true;
		const result = await call(
			api.GET('/api/runs/{run_id}', { params: { path: { run_id: runId } } })
		);
		runLoading = false;
		if (result.ok) run = result.data;
		else runError = result.message;
	}

	async function loadHistory(id: number) {
		historyError = null;
		const result = await call(
			api.GET('/api/deployments', { params: { query: { movie_id: id, page_size: 50 } } })
		);
		if (id !== movieId) return;
		if (result.ok) history = result.data.items;
		else historyError = result.message;
	}

	$effect(() => {
		const id = movieId;
		movie = null;
		run = null;
		history = null;
		if (!Number.isInteger(id) || id < 1) {
			movieError = 'There is no such movie.';
			return;
		}
		void loadMovie(id);
		void loadHistory(id);
	});

	// Run
	let runJob = $state<Job | null>(null);
	let runNote = $state<string | null>(null);
	let runBusy = $state(false);
	let stopRun: () => void = () => {};

	async function startRun() {
		runBusy = true;
		runNote = null;
		const result = await call(
			api.POST('/api/movies/{movie_id}/run', { params: { path: { movie_id: movieId } } })
		);
		if (!result.ok) {
			runBusy = false;
			runNote = result.message;
			return;
		}
		runJob = result.data;
		if (result.status === 200) runNote = `A run of this movie is already ${result.data.state}.`;
		stopRun();
		stopRun = followJob(
			result.data.id,
			(job) => {
				runJob = job;
				if (!isFinal(job.state)) return;
				runBusy = false;
				toast(`Run job ${job.id}: ${describeJob(job)}`, job.state === 'succeeded' ? 'good' : 'bad');
				void loadMovie(movieId);
			},
			(message) => (runNote = message)
		);
	}

	// Restore
	let confirming = $state<Deployment | null>(null);
	let restoreJob = $state<Job | null>(null);
	let restoringId = $state<number | null>(null);
	let restoreNote = $state<string | null>(null);
	let stopRestore: () => void = () => {};

	async function restore(deployment: Deployment) {
		confirming = null;
		restoringId = deployment.id;
		restoreNote = null;
		const result = await call(
			api.POST('/api/deployments/{deployment_id}/restore', {
				params: { path: { deployment_id: deployment.id } }
			})
		);
		if (!result.ok) {
			restoringId = null;
			restoreNote = result.message;
			toast(result.message, 'bad');
			return;
		}
		restoreJob = result.data;
		stopRestore();
		stopRestore = followJob(
			result.data.id,
			(job) => {
				restoreJob = job;
				if (!isFinal(job.state)) return;
				restoringId = null;
				toast(
					`Restore job ${job.id}: ${describeJob(job)}`,
					job.state === 'succeeded' ? 'good' : 'bad'
				);
				void loadMovie(movieId);
				void loadHistory(movieId);
			},
			(message) => (restoreNote = message)
		);
	}

	onDestroy(() => {
		stopRun();
		stopRestore();
	});
</script>

<svelte:head>
	<title>{movie ? `${movie.title} · Marquee` : 'Movie · Marquee'}</title>
</svelte:head>

<a class="back" href="/">← Library</a>

{#if movieError}
	<Notice tone="bad" message={movieError}>
		{#snippet action()}
			<Button onclick={() => loadMovie(movieId)}>Try again</Button>
		{/snippet}
	</Notice>
{:else if !movie}
	<Notice tone="loading" message="Loading the movie…" />
{:else}
	<section class="head">
		<div class="poster">
			{#if movie.poster && !posterBroken}
				<img
					src={thumbnailUrl(movie.poster, 640)}
					alt="Poster of {movie.title}"
					width="640"
					height="960"
					onerror={() => (posterBroken = true)}
				/>
			{:else}
				<div class="none">{movie.poster ? 'Poster unavailable' : 'No poster'}</div>
			{/if}
			<span class="origin" data-testid="poster-origin">{originLabel(movie.poster_origin)}</span>
		</div>
		<div class="facts">
			<h1>{movie.title} <span class="year">{movie.year ?? ''}</span></h1>
			{#if movie.tagline}<p class="tagline">{movie.tagline}</p>{/if}
			<dl>
				{#if movie.genres.length}<dt>Genres</dt>
					<dd>{movie.genres.join(', ')}</dd>{/if}
				{#if movie.director}<dt>Director</dt>
					<dd>{movie.director}</dd>{/if}
				{#if movie.production_companies?.length}
					<dt>Studios</dt>
					<dd>{movie.production_companies.join(', ')}</dd>
				{/if}
				<dt>File</dt>
				<dd>{movie.has_file ? formatBytes(movie.file_size_bytes) : 'Wanted; no file yet'}</dd>
				<dt>Added</dt>
				<dd>{formatWhen(movie.added_at)}</dd>
				<dt>TMDB</dt>
				<dd>{movie.tmdb_id ?? '—'}</dd>
				<dt>IMDb</dt>
				<dd>{movie.imdb_id ?? '—'}</dd>
			</dl>
			<div class="actions">
				<Button variant="primary" busy={runBusy} onclick={startRun}>Run</Button>
				{#if runJob}
					<span class="job" data-testid="run-job">
						Job {runJob.id}: {describeJob(runJob)}
						<a href="/activity?job={runJob.id}">Activity</a>
					</span>
				{/if}
			</div>
			{#if runNote}<p class="note" role="status" data-testid="run-note">{runNote}</p>{/if}
		</div>
	</section>

	<section class="block">
		<h2>Latest run</h2>
		{#if runError}
			<Notice tone="bad" message="The run could not be loaded. {runError}">
				{#snippet action()}
					<Button onclick={() => loadRun(movie?.latest_run_id ?? null)}>Try again</Button>
				{/snippet}
			</Notice>
		{:else if movie.latest_run_id === null}
			<Notice message="This movie has never been run." />
		{:else if !run || runLoading}
			<Notice tone="loading" message="Loading the run…" />
		{:else}
			<p class="run-meta" data-testid="run-meta">
				Run {run.id} · {formatWhen(run.started_at)} · took {formatDuration(
					run.started_at,
					run.finished_at
				)}
				· {runSummary(run)} · {blindLabel(run.blind)} · {run.reviewed ? 'Reviewed' : 'Not reviewed'}
				{#if run.state === 'completed'}
					<a class="review" href="/review/{run.id}" data-testid="review-this-run">Review this run</a
					>
				{/if}
			</p>
			{#if run.candidates.some((c) => c.rank !== null)}
				<CandidateGrid candidates={run.candidates} />
			{:else}
				<Notice message="No candidate was ranked in this run." />
			{/if}
		{/if}
	</section>

	<section class="block">
		<h2>Poster history</h2>
		{#if historyError}
			<Notice tone="bad" message="The poster history could not be loaded. {historyError}">
				{#snippet action()}
					<Button onclick={() => loadHistory(movieId)}>Try again</Button>
				{/snippet}
			</Notice>
		{:else if history === null}
			<Notice tone="loading" message="Loading the poster history…" />
		{:else if history.length === 0}
			<Notice message="Marquee has never written a poster for this movie." />
		{:else}
			{#if restoreJob}
				<p class="note" role="status" data-testid="restore-job">
					Restore job {restoreJob.id}: {describeJob(restoreJob)}
				</p>
			{/if}
			{#if restoreNote}<p class="note bad" role="alert">{restoreNote}</p>{/if}
			<PosterHistory
				deployments={history}
				busyId={restoringId}
				disabled={restoringId !== null}
				onRestore={(deployment) => (confirming = deployment)}
			/>
		{/if}
	</section>
{/if}

<ConfirmDialog
	open={confirming !== null}
	title="Put back what was there before?"
	message={confirming
		? `This writes ${restoreTarget(confirming)} into the movie's folder, as it was before deployment ${confirming.id}. What is there now is backed up first, and this can be undone in turn.`
		: ''}
	confirmLabel="Put it back"
	onConfirm={() => confirming && restore(confirming)}
	onCancel={() => (confirming = null)}
/>

<style>
	.back {
		display: inline-block;
		margin-bottom: 14px;
		color: var(--muted);
		font-size: 13px;
	}
	.back:hover {
		color: var(--text);
	}
	.head {
		display: grid;
		grid-template-columns: minmax(0, 320px) minmax(0, 1fr);
		gap: 24px;
		align-items: start;
	}
	.poster {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.poster img,
	.none {
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel2);
	}
	.none {
		display: grid;
		place-items: center;
		color: var(--faint);
		border-style: dashed;
	}
	.origin {
		font-size: 12px;
		color: var(--muted);
	}
	h1 {
		margin: 0 0 4px;
		font-size: 22px;
		letter-spacing: -0.01em;
	}
	.year {
		color: var(--muted);
		font-weight: 400;
	}
	.tagline {
		margin: 0 0 12px;
		color: var(--muted);
		font-style: italic;
	}
	dl {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: 6px 14px;
		margin: 0 0 16px;
		font-size: 13px;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
		overflow-wrap: anywhere;
	}
	.actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px 12px;
	}
	.job,
	.note {
		font-size: 13px;
		color: var(--muted);
	}
	.job a {
		color: var(--gold-copy);
		margin-left: 6px;
	}
	.note {
		margin: 8px 0;
	}
	.note.bad {
		color: var(--bad);
	}
	.block {
		margin-top: 32px;
	}
	h2 {
		margin: 0 0 10px;
		font-size: 15px;
	}
	.run-meta {
		margin: 0 0 12px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.review {
		margin-left: 8px;
		color: var(--gold-copy);
		font-weight: 550;
	}
	@media (max-width: 720px) {
		.head {
			grid-template-columns: minmax(0, 1fr);
			gap: 16px;
		}
		.poster {
			max-width: 240px;
		}
	}
</style>
