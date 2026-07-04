<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import PosterRankPanel from '$lib/components/PosterRankPanel.svelte';
	import { toast } from '$lib/toast';
	import {
		startOnboarding,
		getTasteTestMovies,
		tasteTestRank,
		completeOnboarding
	} from '$lib/api/onboarding';
	import type { OnboardingStatus, RankItem, TasteTestMovie } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// svelte-ignore state_referenced_locally
	let status = $state<OnboardingStatus | null>(data.status);
	let movies = $state<TasteTestMovie[]>([]);
	let index = $state(0);
	let busy = $state(false);

	const started = $derived(!!status?.path);
	const isTasteTest = $derived(status?.path === 'taste_test');
	const current = $derived(movies[index] ?? null);
	const ranked = $derived(status?.ranked ?? 0);

	const items = $derived.by<RankItem[]>(() =>
		(current?.posters ?? []).map((p) => ({
			key: p.file,
			posterUrl: p.url,
			label: '',
			score: null,
			filenames: [p.file]
		}))
	);

	/** Encouragement copy keyed to the 15 / 25 / 40 milestones. */
	const message = $derived.by(() => {
		if (!status) return '';
		const { min, goal, max } = status;
		if (ranked < min) return `Rank at least ${min} movies to activate — ${goal} is the sweet spot.`;
		if (ranked < goal)
			return `You can activate now. Getting to ${goal} gives the best results, though.`;
		if (ranked < max)
			return `You're all set — activate whenever you like. Or push toward ${max} for an even sharper engine.`;
		return `Maxed out at ${max} — that's plenty. Activate to finish.`;
	});

	async function begin() {
		busy = true;
		try {
			const result = await startOnboarding(fetch, null);
			status = result.status;
			if (result.path === 'taste_test') {
				movies = result.movies ?? (await getTasteTestMovies(fetch)).movies;
				index = 0;
			}
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not start onboarding', 'bad');
		} finally {
			busy = false;
		}
	}

	async function submitRanking(payload: { order: string[]; hated: string[] }) {
		if (!current) return;
		const res = await tasteTestRank(fetch, current.id, payload.order, payload.hated);
		status = res.status;
		toast(`Ranked ${current.title ?? 'movie'}`, 'good');
	}

	function next() {
		if (index < movies.length - 1) index += 1;
	}

	async function complete() {
		busy = true;
		try {
			const res = await completeOnboarding(fetch);
			status = res.status;
			toast('Key Art Engine activated — training your taste now', 'good');
			void goto('/taste');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not complete the rank test', 'bad');
		} finally {
			busy = false;
		}
	}

	onMount(async () => {
		if (status?.complete) return;
		if (status?.path === 'taste_test' && movies.length === 0) {
			try {
				movies = (await getTasteTestMovies(fetch)).movies;
			} catch {
				/* taste test may be unavailable; the start flow re-checks */
			}
		}
	});
</script>

<SectionHeader
	title="Jump-start the Key Art Engine"
	subtitle="A quick taste test teaches the engine your style"
/>

{#if data.error}
	<div class="note bad">{data.error}</div>
{:else if status?.complete}
	<div class="note good">
		<strong>The engine is trained.</strong> You can re-rank any movie later from its results page.
		<a class="link" href="/taste">View the Key Art Engine →</a>
	</div>
{:else if !started}
	<!-- Intro / begin -->
	<div class="intro-card">
		<p>
			The engine starts knowing nothing about your taste. Rank a handful of movies — drag posters
			into your preferred order, drop the ones you dislike into <em>Hate</em> — and it learns what good
			key art means to you.
		</p>
		<ul>
			<li><strong>{status?.min ?? 15}</strong> movies minimum to activate</li>
			<li><strong>{status?.goal ?? 25}</strong> is the sweet spot</li>
			<li>Stop anytime — your progress is saved.</li>
		</ul>
		<button class="btn-gold" onclick={begin} disabled={busy}>
			{busy ? 'Starting…' : 'Begin the taste test'}
		</button>
	</div>
{:else}
	<!-- Progress meter -->
	{#if status}
		<div class="meter">
			<div class="meter-top">
				<span class="count mono">{ranked}<span class="of"> / {status.goal}</span></span>
				<span class="msg">{message}</span>
			</div>
			<ProgressBar
				value={Math.min(100, (ranked / status.max) * 100)}
				tone={ranked >= status.min ? 'good' : 'gold'}
				height={7}
			/>
			<div class="ticks">
				<span class:hit={ranked >= status.min}>{status.min} activate</span>
				<span class:hit={ranked >= status.goal}>{status.goal} goal</span>
				<span class:hit={ranked >= status.max}>{status.max} max</span>
			</div>
		</div>

		<div class="actions">
			<button class="btn-gold" onclick={complete} disabled={busy || !status.can_complete}>
				{busy ? 'Activating…' : 'Complete & activate'}
			</button>
			{#if !status.can_complete}
				<span class="hint">Rank {status.min - ranked} more to activate.</span>
			{/if}
		</div>
	{/if}

	{#if isTasteTest}
		{#if ranked >= (status?.max ?? 40)}
			<div class="note good">You've ranked the max — hit <strong>Complete & activate</strong>.</div>
		{:else if current}
			<div class="movie-head">
				<h3>
					{current.title}{#if current.year}<span class="year"> ({current.year})</span>{/if}
				</h3>
				{#if current.genres.length}<span class="genres">{current.genres.join(' · ')}</span>{/if}
			</div>
			{#key current.id}
				<PosterRankPanel
					{items}
					submitLabel="Save & next"
					onSubmit={submitRanking}
					onsubmitted={next}
				/>
			{/key}
		{:else}
			<div class="note">
				That's every test movie ranked. <strong>Complete & activate</strong> when you're ready.
			</div>
		{/if}
	{:else}
		<!-- Library path: ranking happens on the run-results pages -->
		<div class="note">
			We queued a genre-diverse batch of your library to rank. Review and rank them from the
			<a class="link" href="/pipeline/movies?tab=review">Pipeline review queue</a> — each one you
			rank counts toward the meter above. Come back here to activate when you hit {status?.min ??
				15}.
		</div>
	{/if}
{/if}

<style>
	.intro-card,
	.meter,
	.note {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 16px 18px;
		margin-bottom: 16px;
		background: var(--panel);
	}
	.intro-card p {
		margin: 0 0 10px;
		color: var(--text);
		line-height: 1.5;
	}
	.intro-card ul {
		margin: 0 0 16px;
		padding-left: 18px;
		color: var(--muted);
	}
	.intro-card li {
		margin: 2px 0;
	}
	.meter {
		background: var(--gold-soft);
	}
	.meter-top {
		display: flex;
		align-items: baseline;
		gap: 14px;
		margin-bottom: 8px;
		flex-wrap: wrap;
	}
	.count {
		font-size: 22px;
		color: var(--gold);
		font-weight: 700;
	}
	.of {
		font-size: 14px;
		color: var(--muted);
		font-weight: 400;
	}
	.msg {
		font-size: 13px;
		color: var(--text);
	}
	.ticks {
		display: flex;
		justify-content: space-between;
		margin-top: 6px;
		font-size: 11px;
		color: var(--faint);
	}
	.ticks .hit {
		color: var(--good);
		font-weight: 600;
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: 18px;
	}
	.hint {
		font-size: 12px;
		color: var(--muted);
	}
	.movie-head {
		display: flex;
		align-items: baseline;
		gap: 12px;
		background: transparent;
		border: none;
		padding: 0 2px;
		margin-bottom: 8px;
	}
	.movie-head h3 {
		margin: 0;
		font-size: 17px;
	}
	.year {
		color: var(--muted);
		font-weight: 400;
	}
	.genres {
		font-size: 12px;
		color: var(--muted);
	}
	.note {
		color: var(--muted);
		line-height: 1.5;
	}
	.note.good {
		border-color: var(--good);
		color: var(--text);
	}
	.note.bad {
		border-color: var(--bad);
		color: var(--bad);
	}
	.link {
		color: var(--gold);
		text-decoration: none;
	}
	.mono {
		font-family: var(--font-mono);
	}

	/* Buttons (the app's .btn-* are page-scoped, not global) */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
	}
	.btn-gold:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}
</style>
