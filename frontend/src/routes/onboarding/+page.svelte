<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import {
		chooseOnboardingCandidate,
		completeOnboarding,
		getOnboardingReview,
		getOnboardingStatus,
		hateOnboardingCandidate,
		startOnboarding
	} from '$lib/api/onboarding';
	import type {
		OnboardingProfileLibrary,
		OnboardingReview,
		OnboardingStatus
	} from '$lib/api/types';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// svelte-ignore state_referenced_locally
	let status = $state<OnboardingStatus | null>(data.status);
	// svelte-ignore state_referenced_locally
	let review = $state<OnboardingReview | null>(data.review);
	// svelte-ignore state_referenced_locally
	let reviewError = $state<string | null>(data.reviewError);
	let busy = $state(false);
	let decisionBusy = $state<string | null>(null);
	let analysisSubject = $state<string | null>(null);
	let decisionOutcome = $state<string | null>(null);
	// svelte-ignore state_referenced_locally
	let initiatedJobIds = $state<string[]>(status?.active_jobs.map((job) => job.job_id) ?? []);
	let refreshVersion = 0;
	let refreshAbort: AbortController | null = null;
	const decisionKeys = new SvelteMap<string, string>();

	const count = $derived(status?.active_positive_subjects ?? 0);
	const required = $derived(status?.thresholds.required ?? 50);
	const encouraged = $derived(status?.thresholds.encouraged ?? 75);
	const strongTarget = $derived(status?.thresholds.strong_target ?? 100);
	const personalized = $derived(status?.state === 'personalized');
	const canBuild = $derived(count >= required && status?.state !== 'building');
	const profileRows = $derived(
		status
			? (Object.entries(status.libraries) as Array<
					[keyof OnboardingStatus['libraries'], OnboardingProfileLibrary]
				>)
			: []
	);

	function applyStatus(next: OnboardingStatus) {
		status = next;
		initiatedJobIds = [
			...new Set([...initiatedJobIds, ...next.active_jobs.map((job) => job.job_id)])
		].slice(0, 20);
	}

	async function loadReview(runId: string) {
		try {
			review = await getOnboardingReview(fetch, runId);
			reviewError = null;
			await goto(`/onboarding?review=${encodeURIComponent(runId)}`, {
				replaceState: true,
				keepFocus: true,
				noScroll: true
			});
		} catch (error) {
			review = null;
			reviewError = error instanceof Error ? error.message : 'Could not load the candidate review';
		}
	}

	async function refreshStatus() {
		refreshAbort?.abort();
		const controller = new AbortController();
		refreshAbort = controller;
		const version = ++refreshVersion;
		try {
			const next = await getOnboardingStatus(fetch, controller.signal);
			if (version !== refreshVersion) return;
			applyStatus(next);
			if (next.review && review?.run_id !== next.review.run_id) {
				await loadReview(next.review.run_id);
			}
		} catch (error) {
			if (controller.signal.aborted || version !== refreshVersion) return;
			toast(error instanceof Error ? error.message : 'Could not refresh onboarding status', 'bad');
		} finally {
			if (refreshAbort === controller) refreshAbort = null;
		}
	}

	onMount(() => {
		const timer = window.setInterval(() => {
			if (status?.active_jobs.length) void refreshStatus();
		}, 5000);
		return () => {
			refreshVersion += 1;
			refreshAbort?.abort();
			window.clearInterval(timer);
		};
	});

	async function analyzeNext() {
		busy = true;
		try {
			const result = await startOnboarding(fetch);
			applyStatus(result.status);
			initiatedJobIds = [result.analysis_job.job_id, ...initiatedJobIds].slice(0, 20);
			analysisSubject = result.subject.title;
			decisionOutcome = null;
			toast(`Analyzing neutral choices for ${result.subject.title}`, 'good');
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not analyze the next movie', 'bad');
		} finally {
			busy = false;
		}
	}

	async function buildProfiles() {
		busy = true;
		try {
			const result = await completeOnboarding(fetch);
			applyStatus(result.status);
			initiatedJobIds = result.build_jobs.map((job) => job.job_id);
			toast('Building and validating your movie and TV taste profiles', 'good');
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not start the profile build', 'bad');
		} finally {
			busy = false;
		}
	}

	function decisionKey(candidateId: string, decision: 'choose' | 'hate') {
		const key = `${review?.run_id ?? ''}:${candidateId}:${decision}`;
		const existing = decisionKeys.get(key);
		if (existing) return existing;
		const created = `onboarding:${decision}:${crypto.randomUUID()}`;
		decisionKeys.set(key, created);
		return created;
	}

	async function decide(
		candidate: OnboardingReview['candidates'][number],
		decision: 'choose' | 'hate'
	) {
		if (!review || decisionBusy || !review.allowed_actions[decision]) return;
		decisionBusy = `${candidate.candidate_id}:${decision}`;
		try {
			const intent = {
				run_id: review.run_id,
				candidate_id: candidate.candidate_id,
				review_revision: review.review_revision,
				idempotency_key: decisionKey(candidate.candidate_id, decision)
			};
			const result =
				decision === 'choose'
					? await chooseOnboardingCandidate(fetch, intent)
					: await hateOnboardingCandidate(fetch, intent);
			applyStatus(result.status);
			review = {
				...review,
				allowed_actions: { choose: false, hate: false }
			};
			if (result.deployment_job_id) {
				initiatedJobIds = [result.deployment_job_id, ...initiatedJobIds].slice(0, 20);
				decisionOutcome =
					'Your choice was recorded. Marquee is validating the selected poster deployment.';
			} else {
				decisionOutcome = 'Your dislike was recorded. No poster deployment was created.';
			}
			toast(decisionOutcome, 'good');
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not record that decision', 'bad');
		} finally {
			decisionBusy = null;
		}
	}

	function ocrSummary(value: unknown): string {
		if (typeof value === 'string' && value) return value;
		if (value && typeof value === 'object' && 'summary' in value) {
			const summary = (value as { summary?: unknown }).summary;
			if (typeof summary === 'string' && summary) return summary;
		}
		return 'Passed the recorded OCR and eligibility checks.';
	}

	function subjectTitle(subject: Record<string, unknown>) {
		return typeof subject.title === 'string' ? subject.title : 'Current movie';
	}

	function libraryName(library: keyof OnboardingStatus['libraries']) {
		return library === 'movies' ? 'Movie profile' : 'TV profile';
	}
</script>

<SectionHeader
	title="Teach Marquee your poster taste"
	subtitle="Choose posters you genuinely prefer; Marquee keeps every choice neutral while it learns"
/>

{#if data.error}
	<div class="notice bad" role="alert">{data.error}</div>
{:else if status}
	<div class="journey">
		<div class="count"><strong>{count} of {required}</strong> required choices</div>
		<ProgressBar
			value={Math.min(100, (count / strongTarget) * 100)}
			tone={count >= required ? 'good' : 'gold'}
			height={8}
		/>
		<div class="milestones">
			<span class:reached={count >= required}>{required} required</span>
			<span class:reached={count >= encouraged}>{encouraged} recommended</span>
			<span class:reached={count >= strongTarget}>{strongTarget} strong target</span>
		</div>
		<p>{status.next_action}</p>
		<p class="fine">
			Only an explicit choice followed by a successful, validated poster deployment counts.
			Duplicate movies or identical poster bytes do not increase this total.
		</p>
	</div>

	{#if personalized}
		<div class="notice good">
			<strong>Your taste profiles are active.</strong> Both movie and TV consumers reloaded the published
			profile revision. You can keep refining your taste here at any time.
		</div>
	{:else if status.state === 'building'}
		<div class="notice">
			Marquee is building, validating, publishing, and reloading both profiles.
		</div>
	{:else if status.state === 'degraded'}
		<div class="notice bad">
			The latest build needs attention. Your existing evidence remains safe; use the build detail
			below to retry with normal successor lineage.
		</div>
	{/if}

	<div class="actions">
		<button
			class="btn-gold"
			type="button"
			onclick={analyzeNext}
			disabled={busy || status.state === 'building'}
		>
			{busy ? 'Working…' : 'Choose for another movie'}
		</button>
		{#if canBuild}
			<button class="btn-secondary" type="button" onclick={buildProfiles} disabled={busy}>
				Build taste profiles
			</button>
		{/if}
		<a class="link" href="/projection-room?feature_area=ml_taste">Open Activity and evidence →</a>
	</div>

	{#if analysisSubject}
		<div class="notice" aria-live="polite">
			<strong>Analyzing {analysisSubject}.</strong> When its neutral choices are ready, this page will
			return to the review automatically. You can also follow the analysis below.
		</div>
	{/if}

	{#if reviewError}
		<div class="notice bad" role="alert">
			{reviewError} Refresh the page or open the analysis details to confirm whether its retained review
			evidence is still available.
		</div>
	{/if}

	{#if review}
		<section class="review" aria-labelledby="candidate-review-title" data-testid="candidate-review">
			<header>
				<div>
					<h2 id="candidate-review-title">Choose a poster for {subjectTitle(review.subject)}</h2>
					<p>
						These choices survived the same objective checks. None is preselected; choose one you
						genuinely prefer, or mark one you do not want.
					</p>
				</div>
				<nav aria-label="Analysis evidence">
					<a href={review.links.activity}>Analysis Activity</a>
					<a href={review.links.detail}>Analysis details</a>
				</nav>
			</header>
			{#if decisionOutcome}
				<div class="decision-outcome" aria-live="polite">{decisionOutcome}</div>
			{/if}
			<div class="candidate-grid">
				{#each review.candidates as candidate (candidate.candidate_id)}
					<article class="candidate-card">
						<img src={candidate.image_url} alt={`Poster choice from ${candidate.source}`} />
						<div class="candidate-copy">
							<h3>Poster choice</h3>
							<p><strong>Source:</strong> {candidate.source}</p>
							<p>
								<strong>Eligibility:</strong>
								{candidate.eligibility.status.replaceAll('_', ' ')}
							</p>
							<p><strong>OCR:</strong> {ocrSummary(candidate.eligibility.ocr)}</p>
							{#if Object.entries(candidate.facts).length}
								<dl>
									{#each Object.entries(candidate.facts) as [name, value] (name)}
										<dt>{name}</dt>
										<dd>{String(value)}</dd>
									{/each}
								</dl>
							{/if}
						</div>
						<div class="candidate-actions">
							<button
								class="btn-gold"
								type="button"
								onclick={() => decide(candidate, 'choose')}
								disabled={!review.allowed_actions.choose || decisionBusy !== null}
							>
								{decisionBusy === `${candidate.candidate_id}:choose`
									? 'Recording…'
									: 'Choose this poster'}
							</button>
							<button
								class="btn-secondary"
								type="button"
								onclick={() => decide(candidate, 'hate')}
								disabled={!review.allowed_actions.hate || decisionBusy !== null}
							>
								{decisionBusy === `${candidate.candidate_id}:hate`
									? 'Recording…'
									: 'I do not want this'}
							</button>
						</div>
					</article>
				{/each}
			</div>
			<p class="fine">
				Objective rejection details remain available in the retained analysis evidence.
			</p>
		</section>
	{/if}

	<section class="profiles" aria-labelledby="profile-builds-title">
		<h2 id="profile-builds-title">Taste profile publication</h2>
		<div class="profile-grid">
			{#each profileRows as [library, profile] (library)}
				<article class="profile-row" data-ready={profile.active.compatible}>
					<h3>{libraryName(library)}</h3>
					<p>
						{#if profile.active.compatible}
							Active generation {profile.active.generation}; consumer reload confirmed.
						{:else}
							No compatible active publication yet.
						{/if}
					</p>
					<p>Current build: {profile.build.state ?? 'waiting for evidence'}.</p>
					{#if profile.update_attention}
						<p class="attention">
							A later update needs attention; the compatible active profile remains in use.
						</p>
					{/if}
					{#if profile.build.failure}
						<p class="attention">
							Open this build to review the failure and create a normal retry successor.
						</p>
					{/if}
					{#if profile.build.job_id}
						<a href={`/projection-room/jobs/${profile.build.job_id}`}>Open build details</a>
					{/if}
				</article>
			{/each}
		</div>
	</section>

	<FeatureActivityPanel
		scopeKey="feature:onboarding:taste"
		query={{ feature_area: 'ml_taste' }}
		jobIds={initiatedJobIds}
		heading="Taste onboarding activity"
	/>
{/if}

<style>
	.journey,
	.notice,
	.review,
	.profiles {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		padding: 16px 18px;
		margin-bottom: 16px;
	}
	.journey {
		background: var(--gold-soft);
	}
	.count {
		margin-bottom: 10px;
		font-size: 18px;
		color: var(--gold);
	}
	.milestones {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		margin-top: 7px;
		font-size: 11px;
		color: var(--faint);
	}
	.milestones .reached {
		color: var(--good);
		font-weight: 650;
	}
	.fine {
		margin-bottom: 0;
		font-size: 12px;
		line-height: 1.5;
		color: var(--muted);
	}
	.notice.good,
	.profile-row[data-ready='true'] {
		border-color: color-mix(in srgb, var(--good) 45%, var(--line));
	}
	.notice.bad {
		border-color: color-mix(in srgb, var(--bad) 45%, var(--line));
	}
	.actions,
	.review header,
	.review header nav,
	.candidate-actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
	}
	.actions {
		margin-bottom: 18px;
	}
	.review header {
		justify-content: space-between;
		align-items: start;
		margin-bottom: 16px;
	}
	.review h2,
	.profiles h2,
	.review h3,
	.profile-row h3 {
		margin-top: 0;
	}
	.review header p {
		max-width: 68ch;
		margin-bottom: 0;
		color: var(--muted);
	}
	.decision-outcome {
		margin-bottom: 14px;
		padding: 10px 12px;
		border-radius: var(--radius-sm);
		background: var(--good-soft);
		color: var(--good);
	}
	.candidate-grid,
	.profile-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
		gap: 14px;
	}
	.candidate-card,
	.profile-row {
		min-width: 0;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
		background: var(--panel-raised);
	}
	.candidate-card img {
		display: block;
		width: 100%;
		aspect-ratio: 2 / 3;
		object-fit: cover;
		background: var(--surface);
	}
	.candidate-copy,
	.candidate-actions,
	.profile-row {
		padding: 14px;
	}
	.candidate-copy p,
	.profile-row p {
		margin: 7px 0;
		font-size: 13px;
		line-height: 1.45;
	}
	dl {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 4px 8px;
		font-size: 12px;
	}
	dt {
		color: var(--muted);
	}
	dd {
		margin: 0;
		word-break: break-word;
	}
	.candidate-actions {
		padding-top: 0;
	}
	.attention {
		color: var(--gold);
	}
	.profile-row a,
	.review a,
	.link {
		color: var(--gold);
		font-size: 13px;
	}
	@media (max-width: 520px) {
		.milestones,
		.review header,
		.review header nav {
			align-items: start;
			flex-direction: column;
		}
		.candidate-grid,
		.profile-grid {
			grid-template-columns: 1fr;
		}
		.candidate-actions > button {
			width: 100%;
		}
	}
</style>
