<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, call } from '$lib/api/client';
	import type {
		FeedbackEvent,
		FeedbackRequest,
		FeedbackResult,
		Movie,
		RunDetail
	} from '$lib/api/types';
	import { originLabel, thumbnailUrl } from '$lib/features/library/poster';
	import AnswerResult from '$lib/features/review/AnswerResult.svelte';
	import EventHistory from '$lib/features/review/EventHistory.svelte';
	import {
		AnswerKeys,
		approvalAnswer,
		groupIntoCards,
		initialRanking,
		initialShown,
		overrideAnswer,
		rankAnswer,
		rankProblem,
		rejectAnswer,
		shownVariant,
		withKey,
		worthRetrying,
		type Answer,
		type Ranking,
		type Shown
	} from '$lib/features/review/panel';
	import RankPanel from '$lib/features/review/RankPanel.svelte';
	import {
		nextRun,
		rankConfirmation,
		rejectConfirmation,
		replaceConfirmation
	} from '$lib/features/review/review';
	import CandidateGrid from '$lib/features/runs/CandidateGrid.svelte';
	import { blindLabel, runSummary } from '$lib/features/runs/runs';
	import { formatDuration, formatWhen } from '$lib/format';
	import { refreshPendingReviews } from '$lib/stores/review';
	import { toast } from '$lib/stores/toast';
	import Button from '$lib/ui/Button.svelte';
	import ConfirmDialog from '$lib/ui/ConfirmDialog.svelte';
	import Notice from '$lib/ui/Notice.svelte';
	import { randomUuid } from '$lib/uuid';

	const runId = $derived(Number(page.params.runId));

	let run = $state<RunDetail | null>(null);
	let runError = $state<string | null>(null);
	let movie = $state<Movie | null>(null);
	/** The movie's latest completed run: undefined while it is read. */
	let latest = $state<number | null | undefined>(undefined);
	let events = $state<FeedbackEvent[] | null>(null);
	let eventsError = $state<string | null>(null);
	let posterBroken = $state(false);

	const cards = $derived(run ? groupIntoCards(run.candidates) : []);
	let shown = $state<Shown>({});
	let ranking = $state<Ranking>({ order: [], hated: [] });
	let deploy = $state(true);

	const readOnly = $derived.by(() => {
		if (!run) return null;
		if (run.state !== 'completed') {
			return `This run is ${run.state}. Only a completed run can be answered.`;
		}
		if (latest !== undefined && latest !== run.id) {
			return latest === null
				? 'The movie has no completed run to answer.'
				: 'This is not the movie’s latest completed run. Feedback goes to that one.';
		}
		if (cards.length === 0) {
			return 'No candidate was ranked in this run, so there is nothing to answer with.';
		}
		return null;
	});

	async function loadRun(id: number) {
		runError = null;
		const result = await call(api.GET('/api/runs/{run_id}', { params: { path: { run_id: id } } }));
		if (id !== runId) return;
		if (!result.ok) {
			runError = result.status === 404 ? 'There is no such run.' : result.message;
			return;
		}
		run = result.data;
		const fresh = groupIntoCards(result.data.candidates);
		shown = initialShown(fresh);
		ranking = initialRanking(fresh);
		await Promise.all([loadMovie(result.data.movie_id, id), loadLatest(result.data.movie_id, id)]);
	}

	async function loadMovie(movieId: number, id: number) {
		const result = await call(
			api.GET('/api/movies/{movie_id}', { params: { path: { movie_id: movieId } } })
		);
		if (id !== runId) return;
		if (result.ok) {
			movie = result.data;
			posterBroken = false;
		}
	}

	async function loadLatest(movieId: number, id: number) {
		const result = await call(
			api.GET('/api/runs', {
				params: { query: { movie_id: movieId, state: 'completed', page_size: 1 } }
			})
		);
		if (id !== runId) return;
		latest = result.ok ? (result.data.items[0]?.id ?? null) : undefined;
	}

	async function loadEvents(id: number) {
		eventsError = null;
		const result = await call(
			api.GET('/api/runs/{run_id}/feedback', { params: { path: { run_id: id } } })
		);
		if (id !== runId) return;
		if (result.ok) events = result.data.items;
		else eventsError = result.message;
	}

	$effect(() => {
		const id = runId;
		run = null;
		movie = null;
		latest = undefined;
		events = null;
		answer = null;
		failure = null;
		historyNote = null;
		nextNote = null;
		if (!Number.isInteger(id) || id < 1) {
			runError = 'There is no such run.';
			return;
		}
		void loadRun(id);
		void loadEvents(id);
	});

	// ── Answers ──────────────────────────────────────────────────────────

	const keys = new AnswerKeys(randomUuid);
	let sending = $state<string | null>(null);
	let answer = $state<{
		result: FeedbackResult;
		status: number;
		asked: string;
		deployAsked: boolean;
		undone: FeedbackResult | null;
	} | null>(null);
	let failure = $state<{ message: string; retry: Answer | null } | null>(null);
	let confirming = $state<{
		title: string;
		message: string;
		label: string;
		tone: 'gold' | 'bad';
		answer: Answer;
	} | null>(null);

	async function send(next: Answer, retry = false) {
		confirming = null;
		failure = null;
		sending = next.action;
		// `deploy` has a default in the API, so the generated type calls it required; "None of
		// these" sends none (decision 2), and the API ignores it for a reject anyway.
		const body = withKey(next, keys.keyFor(next, retry)) as FeedbackRequest;
		const result = await call(
			api.POST('/api/runs/{run_id}/feedback', { params: { path: { run_id: runId } }, body })
		);
		sending = null;
		if (result.ok) {
			keys.settled();
			answer = {
				result: result.data,
				status: result.status,
				asked: next.action,
				deployAsked: next.deploy ?? false,
				undone: null
			};
			void loadEvents(runId);
			void refreshPendingReviews();
			return;
		}
		if (worthRetrying(result.status)) {
			failure = { message: result.message, retry: next };
			return;
		}
		keys.settled();
		failure = { message: result.message, retry: null };
	}

	/** Send now, or ask first when the decision says to. */
	function ask(
		next: Answer,
		confirmation: { title: string; message: string; label: string; tone?: 'gold' | 'bad' } | null
	) {
		if (!confirmation) {
			void send(next);
			return;
		}
		confirming = { tone: 'gold', ...confirmation, answer: next };
	}

	const pickCard = $derived(cards.find((card) => card.variants.some((v) => v.is_pick)));
	const problem = $derived(rankProblem(cards, shown, ranking));

	function keepPick() {
		const message = replaceConfirmation('the pick', deploy, movie?.poster_origin);
		ask(
			approvalAnswer(cards, shown, deploy),
			message ? { title: 'Replace your own poster?', message, label: 'Keep the pick' } : null
		);
	}

	function useThis(candidateId: number) {
		const rank = run?.candidates.find((c) => c.id === candidateId)?.rank;
		const message = replaceConfirmation(`the design ranked ${rank}`, deploy, movie?.poster_origin);
		ask(
			overrideAnswer(cards, shown, candidateId, deploy),
			message ? { title: 'Replace your own poster?', message, label: 'Use this one' } : null
		);
	}

	function saveRanking() {
		const top = cards.find((card) => card.key === ranking.order[0]);
		const first = top ? `The design ranked ${shownVariant(top, shown).rank}` : 'The first design';
		ask(rankAnswer(cards, shown, ranking, deploy), {
			title: 'Save this ranking?',
			message: rankConfirmation(first, ranking.hated.length, deploy),
			label: 'Save this ranking'
		});
	}

	function noneOfThese() {
		ask(rejectAnswer(cards, shown), {
			title: 'None of these?',
			message: rejectConfirmation(cards.length),
			label: 'None of these',
			tone: 'bad'
		});
	}

	// ── Undo and Next ────────────────────────────────────────────────────

	let undoBusy = $state(false);
	let undoError = $state<string | null>(null);
	let historyBusy = $state<number | null>(null);
	let historyNote = $state<string | null>(null);
	let nextBusy = $state(false);
	let nextNote = $state<string | null>(null);

	async function revoke(eventId: number) {
		return call(
			api.POST('/api/feedback/{event_id}/revoke', { params: { path: { event_id: eventId } } })
		);
	}

	async function undoAnswer() {
		if (!answer) return;
		undoBusy = true;
		undoError = null;
		const result = await revoke(answer.result.event.id);
		undoBusy = false;
		if (!result.ok) {
			undoError = result.message;
			return;
		}
		answer.undone = result.data;
		void loadEvents(runId);
		void refreshPendingReviews();
	}

	async function undoEvent(event: FeedbackEvent) {
		historyBusy = event.id;
		historyNote = null;
		const result = await revoke(event.id);
		historyBusy = null;
		if (!result.ok) {
			toast(result.message, 'bad');
			return;
		}
		historyNote = `Answer ${event.id} was undone. The poster in the folder was not put back.`;
		if (answer?.result.event.id === event.id) answer.undone = result.data;
		void loadEvents(runId);
		void refreshPendingReviews();
	}

	async function goNext() {
		nextBusy = true;
		nextNote = null;
		const result = await call(api.GET('/api/review'));
		nextBusy = false;
		if (!result.ok) {
			nextNote = result.message;
			return;
		}
		const next = nextRun(result.data.items, runId);
		if (next) await goto(`/review/${next.run_id}`);
		else nextNote = 'Nothing else is waiting for a review.';
	}
</script>

<svelte:head>
	<title>{movie ? `Review ${movie.title} · Marquee` : 'Review · Marquee'}</title>
</svelte:head>

<a class="back" href="/review">← Review</a>

{#if runError}
	<Notice tone="bad" message={runError}>
		{#snippet action()}
			<Button onclick={() => loadRun(runId)}>Try again</Button>
		{/snippet}
	</Notice>
{:else if !run}
	<Notice tone="loading" message="Loading the run…" />
{:else}
	<section class="head">
		<div class="poster">
			{#if movie?.poster && !posterBroken}
				<img
					src={thumbnailUrl(movie.poster, 320)}
					alt="The poster in the folder now"
					width="320"
					height="480"
					onerror={() => (posterBroken = true)}
				/>
			{:else}
				<div class="none">{movie?.poster ? 'Poster unavailable' : 'No poster'}</div>
			{/if}
			<span class="origin" data-testid="review-poster-origin">
				{movie ? originLabel(movie.poster_origin) : ''}
			</span>
		</div>
		<div class="facts">
			<h1>
				{#if movie}
					<a href="/movies/{movie.id}">{movie.title}</a>
					<span class="year">{movie.year ?? ''}</span>
				{:else}
					Run {run.id}
				{/if}
			</h1>
			<p class="run-meta" data-testid="review-run-meta">
				Run {run.id} · {formatWhen(run.started_at)} · took {formatDuration(
					run.started_at,
					run.finished_at
				)} · {runSummary(run)} · {blindLabel(run.blind)} · {run.reviewed
					? 'Reviewed'
					: 'Not reviewed'}
			</p>
		</div>
	</section>

	{#if answer}
		<AnswerResult
			result={answer.result}
			status={answer.status}
			asked={answer.asked}
			deployAsked={answer.deployAsked}
			movieId={run.movie_id}
			undone={answer.undone}
			{undoBusy}
			{undoError}
			{nextBusy}
			{nextNote}
			onUndo={undoAnswer}
			onNext={goNext}
		/>
	{/if}

	{#if eventsError}
		<Notice tone="bad" message="The earlier answers could not be loaded. {eventsError}" />
	{:else if events && events.length > 0}
		<section class="block">
			<h2>Earlier answers</h2>
			{#if historyNote}
				<p class="note" role="status">
					{historyNote}
					<a href="/movies/{run.movie_id}">Restore it from the movie’s poster history</a>.
				</p>
			{/if}
			<EventHistory {events} busyId={historyBusy} onUndo={undoEvent} />
		</section>
	{/if}

	<section class="block">
		{#if readOnly}
			<Notice message={readOnly} />
			{#if latest && latest !== run.id}
				<p class="note"><a href="/review/{latest}">Review the latest run ({latest})</a></p>
			{/if}
			{#if cards.length > 0}<CandidateGrid candidates={run.candidates} />{/if}
		{:else}
			<h2>{events && events.length > 0 ? 'Answer again' : 'Your answer'}</h2>
			<div class="answers" data-testid="answers">
				<label class="deploy">
					<input type="checkbox" bind:checked={deploy} data-testid="deploy-checkbox" />
					Write the chosen poster to the movie’s folder
				</label>
				<div class="buttons">
					<Button
						variant="primary"
						busy={sending === 'approval'}
						disabled={sending !== null || !pickCard}
						onclick={keepPick}>Keep the pick</Button
					>
					<Button
						busy={sending === 'rank'}
						disabled={sending !== null || problem !== null}
						title={problem ?? undefined}
						onclick={saveRanking}>Save this ranking</Button
					>
					<Button
						variant="danger"
						busy={sending === 'reject'}
						disabled={sending !== null}
						onclick={noneOfThese}>None of these</Button
					>
				</div>
				{#if problem}<p class="note" data-testid="rank-problem">{problem}</p>{/if}
				{#if sending === 'override'}<p class="note" role="status">Sending your choice…</p>{/if}
			</div>
			{#if failure}
				<Notice tone="bad" message="Your answer was not recorded. {failure.message}">
					{#snippet action()}
						{#if failure?.retry}
							{@const retry = failure.retry}
							<Button onclick={() => send(retry, true)}>Try again</Button>
						{/if}
					{/snippet}
				</Notice>
			{/if}
			<RankPanel {cards} bind:shown bind:ranking disabled={sending !== null} onUse={useThis} />
		{/if}
	</section>
{/if}

<ConfirmDialog
	open={confirming !== null}
	title={confirming?.title ?? ''}
	message={confirming?.message ?? ''}
	confirmLabel={confirming?.label ?? 'Confirm'}
	tone={confirming?.tone ?? 'gold'}
	onConfirm={() => confirming && send(confirming.answer)}
	onCancel={() => (confirming = null)}
/>

<style>
	.back {
		display: inline-block;
		margin-bottom: 14px;
		color: var(--muted);
		font-size: 13px;
	}
	.head {
		display: grid;
		grid-template-columns: 120px minmax(0, 1fr);
		gap: 18px;
		align-items: start;
		margin-bottom: 18px;
	}
	.poster {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.poster img,
	.none {
		width: 100%;
		height: auto;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel2);
	}
	.none {
		display: grid;
		place-items: center;
		color: var(--faint);
		font-size: 12px;
		border-style: dashed;
	}
	.origin {
		font-size: 12px;
		color: var(--muted);
	}
	h1 {
		margin: 0 0 6px;
		font-size: 21px;
	}
	h1 a {
		color: var(--text);
	}
	.year {
		color: var(--muted);
		font-weight: 400;
	}
	.run-meta {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.block {
		margin-top: 22px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	h2 {
		margin: 0;
		font-size: 15px;
	}
	.answers {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 12px 14px;
		border-radius: var(--radius);
		border: 1px solid var(--line);
		background: var(--panel);
	}
	.deploy {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
	}
	.buttons {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.note {
		margin: 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.note a {
		color: var(--gold-copy);
	}
	@media (max-width: 720px) {
		.head {
			grid-template-columns: 88px minmax(0, 1fr);
			gap: 12px;
		}
	}
</style>
