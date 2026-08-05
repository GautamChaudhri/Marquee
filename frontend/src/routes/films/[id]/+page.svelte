<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import { deleteMoviePoster } from '$lib/api/library';
	import { setMovieTextProfile, type TextProfile } from '$lib/api/text-profiles';
	import { posterStatusMeta, toneVar } from '$lib/display';
	import { ApiError } from '$lib/api/client';
	import { toast } from '$lib/toast';
	import type { PipelineRunSummary, RunResults } from '$lib/api/types';
	import { triggerRun, listMovieRuns, getRunResults } from '$lib/api/pipeline';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const movie = $derived(data.movie);
	// svelte-ignore state_referenced_locally
	let tab = $state(data.tab ?? 'poster');
	const textProfiles = $derived(data.textProfiles?.scopes.movie.profiles ?? []);
	// svelte-ignore state_referenced_locally
	let movieTextProfileId = $state<string | null>(data.movieTextProfile?.profile_id ?? null);
	// svelte-ignore state_referenced_locally
	let effectiveTextProfileId = $state(
		data.movieTextProfile?.effective_id ??
			data.textProfiles?.scopes.movie.default_id ??
			'title_only'
	);
	let savingTextProfile = $state(false);
	const effectiveTextProfile = $derived(
		textProfiles.find((profile) => profile.id === effectiveTextProfileId) ?? null
	);

	function setTab(id: string) {
		tab = id;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- transient query builder, not reactive state
		const sp = new URLSearchParams(page.url.searchParams);
		sp.set('tab', id);
		goto(`/films/${movie?.id}?${sp.toString()}`, {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	const TABS = [
		{ id: 'poster', label: 'Poster' },
		{ id: 'activity', label: 'Activity' }
	];

	// ── Pipeline ─────────────────────────────────────────────────────────────
	let pipeRunning = $state(false);
	let pipeError = $state<string | null>(null);
	let pipeRunId = $state<string | null>(null);
	let posterJobIds = $state<string[]>([]);
	let posterScopeActive = $state(false);
	let runs = $state<PipelineRunSummary[]>([]);
	let latest = $state<RunResults | null>(null);
	let posterLoaded = false;

	async function startPipeline() {
		if (!movie) return;
		pipeRunning = true;
		pipeError = null;
		pipeRunId = null;
		try {
			const ref = await triggerRun(fetch, movie.id);
			pipeRunId = ref.job_id;
			posterJobIds = [...posterJobIds, ref.job_id];
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				const detail = (e.body as { detail?: { message?: string; active_run_id?: string } })
					?.detail;
				pipeError = detail?.message ?? 'A run is already in progress';
				if (detail?.active_run_id) pipeRunId = detail.active_run_id;
			} else {
				pipeError = e instanceof Error ? e.message : 'Failed to start pipeline';
			}
			pipeRunning = false;
		}
	}

	async function loadRuns() {
		if (!movie) return;
		try {
			const r = await listMovieRuns(fetch, movie.id);
			runs = r.runs;
			const done = r.runs.find((x) => x.status !== 'running');
			if (done) await loadLatest(done.run_id);
		} catch {
			/* no history yet */
		}
	}
	async function loadLatest(runId: string) {
		try {
			const res = await getRunResults(fetch, runId);
			if ('ranked' in res) {
				latest = res as RunResults;
				if (!pipeRunId) pipeRunId = runId;
			}
		} catch {
			/* ignore */
		}
	}

	let deletingPoster = $state(false);
	let deletePosterError = $state<string | null>(null);

	async function handleDeletePoster() {
		if (!movie) return;
		if (
			!confirm(
				'Are you sure you want to delete the deployed poster? This will remove the file from the film folder and reset the status to missing.'
			)
		) {
			return;
		}
		deletingPoster = true;
		deletePosterError = null;
		try {
			const job = await deleteMoviePoster(fetch, movie.id);
			posterJobIds = [...posterJobIds, job.job_id];
			deletePosterError = 'Poster reset queued; progress is shown below.';
		} catch (e) {
			deletePosterError = e instanceof Error ? e.message : 'Failed to delete poster';
		} finally {
			deletingPoster = false;
		}
	}

	$effect(() => {
		if (tab === 'poster' && !posterLoaded) {
			posterLoaded = true;
			loadRuns();
		}
	});

	async function handlePosterJobSettled(snapshot: JobSnapshotResponse) {
		pipeRunning = false;
		toast(
			`${snapshot.label} ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		await loadRuns();
	}

	function basename(p: string | null | undefined): string {
		if (!p) return '—';
		return p.split('/').pop() ?? p;
	}

	function textProfileLabel(profile: TextProfile | null): string {
		return profile?.name ?? 'Title Only';
	}

	function apiText(error: unknown, fallback: string): string {
		if (error instanceof ApiError) {
			const detail = (error.body as { detail?: string } | undefined)?.detail;
			if (typeof detail === 'string' && detail) return detail;
		}
		return error instanceof Error ? error.message : fallback;
	}

	async function saveMovieTextProfile(profileId: string | null) {
		if (!movie || savingTextProfile) return;
		savingTextProfile = true;
		try {
			const saved = await setMovieTextProfile(fetch, movie.id, profileId);
			movieTextProfileId = saved.profile_id;
			effectiveTextProfileId = saved.effective_id;
			toast(
				profileId ? 'Film text profile saved' : 'Film text profile reset to the default',
				'good'
			);
		} catch (e) {
			toast(apiText(e, 'Failed to save film text profile'), 'bad');
		} finally {
			savingTextProfile = false;
		}
	}
</script>

{#if data.error || !movie}
	<div class="errstate">
		<Icon name="film" size={40} stroke={1} />
		<strong>Could not load film</strong>
		<span>{data.error ?? 'Unknown error'}</span>
		<button onclick={() => goto('/films')}>← Back to Films</button>
	</div>
{:else}
	<div class="crumb">
		<a href="/films">Films</a>
		<span>/</span>
		<span>{movie.title}</span>
	</div>

	<div class="hub">
		<!-- ── Left rail ───────────────────────────────────────── -->
		<aside class="rail">
			<div class="poster-wrap">
				<PosterThumb
					title={movie.title}
					year={movie.year}
					posterStatus={movie.poster_status}
					posterUrl={movie.poster_url}
				/>
			</div>

			<div class="rail-meta">
				<div class="movie-title">{movie.title}</div>
				<div class="movie-sub">
					{movie.year ?? '—'}{#if movie.genres?.length}
						· {movie.genres.slice(0, 2).join(', ')}{/if}
				</div>
			</div>

			<div class="chips">
				{#if movie.resolution}
					<div class="chip">
						<span class="chip-label">Resolution</span>
						<span class="chip-val mono">{movie.resolution}</span>
					</div>
				{/if}
				{#if movie.container}
					<div class="chip">
						<span class="chip-label">Container</span>
						<span class="chip-val mono">{movie.container}</span>
					</div>
				{/if}
				<div class="chip">
					<span class="chip-label">Poster</span>
					<span
						class="chip-status"
						style="--c:{toneVar(posterStatusMeta[movie.poster_status].tone)}"
					>
						<StatusDot tone={posterStatusMeta[movie.poster_status].tone} size={6} />
						{posterStatusMeta[movie.poster_status].label}
					</span>
				</div>
				{#if movie.media_file_path}
					<div class="chip chip-file">
						<span class="chip-label">File</span>
						<span class="chip-val mono small" title={movie.media_file_path}
							>{basename(movie.media_file_path)}</span
						>
					</div>
				{/if}
			</div>
		</aside>

		<!-- ── Right pane ──────────────────────────────────────── -->
		<div class="pane">
			<div class="tabwrap">
				<TabBar tabs={TABS} active={tab} onSelect={setTab} />
			</div>

			<!-- ═══ POSTER TAB ═══════════════════════════════════ -->
			{#if tab === 'poster'}
				<div class="tab-content">
					<div class="section-label">AI Pipeline</div>

					<div class="poster-status-row">
						<div class="status-card">
							<div class="sc-label">Current status</div>
							<div class="sc-val" style="--c:{toneVar(posterStatusMeta[movie.poster_status].tone)}">
								<StatusDot tone={posterStatusMeta[movie.poster_status].tone} />
								{posterStatusMeta[movie.poster_status].label}
							</div>
						</div>
						{#if movie.tmdb_id}
							<button
								class="btn-gold"
								onclick={startPipeline}
								disabled={pipeRunning || posterScopeActive}
							>
								{#if pipeRunning}
									<span class="spin">⟳</span> Running…
								{:else}
									Run AI pipeline
								{/if}
							</button>
						{:else}
							<div class="info-note">No TMDB ID — sync Radarr first</div>
						{/if}
						{#if movie.poster_status !== 'missing'}
							<button
								class="btn-danger"
								onclick={handleDeletePoster}
								disabled={deletingPoster || posterScopeActive}
							>
								{#if deletingPoster}
									<span class="spin">⟳</span> Deleting…
								{:else}
									Delete poster
								{/if}
							</button>
						{/if}
					</div>

					{#if data.textProfiles}
						<div class="status-card profile-card">
							<div class="sc-label">Text profile</div>
							<div class="profile-row">
								<select
									class="profile-select"
									value={movieTextProfileId ?? ''}
									disabled={savingTextProfile}
									onchange={(e) =>
										saveMovieTextProfile((e.currentTarget as HTMLSelectElement).value || null)}
								>
									<option value="">
										Use default ({textProfileLabel(effectiveTextProfile)})
									</option>
									{#each textProfiles as profile (profile.id)}
										<option value={profile.id}>{profile.name}</option>
									{/each}
								</select>
								<div class="profile-meta">
									<span>Effective: {textProfileLabel(effectiveTextProfile)}</span>
									<span> Applies to future pipeline runs for this film’s poster selection. </span>
								</div>
							</div>
						</div>
					{/if}

					{#if deletePosterError}
						<div class="alert-box err">
							{deletePosterError}
						</div>
					{/if}

					{#if pipeError}
						<div class="alert-box err">
							{pipeError}{#if pipeRunId}<span class="mono small">
									· run {pipeRunId.slice(0, 8)}</span
								>{/if}
						</div>
					{/if}

					<FeatureActivityPanel
						scopeKey={`feature:film:posters:${movie.id}`}
						query={{
							feature_area: 'ai_posters',
							types: ['poster_pipeline', 'poster_deploy', 'poster_restore', 'poster_reset'],
							subject_kind: 'movie',
							subject_reference: [String(movie.id)]
						}}
						jobIds={posterJobIds}
						bind:active={posterScopeActive}
						heading="Poster activity"
						onSettled={handlePosterJobSettled}
					/>

					{#if latest}
						<a class="review-summary" href={`/pipeline/runs/${latest.run_id}`}>
							{#if latest.auto_pick}
								<div class="rs-poster">
									<img src={latest.auto_pick.poster_url} alt="Auto-pick" />
								</div>
							{/if}
							<div class="rs-body">
								<div class="rs-title">Auto-pick ready</div>
								<div class="rs-counts mono">
									{latest.ranked.length} ranked · {latest.rejected_by_stage.reduce(
										(a, g) => a + g.count,
										0
									)} rejected
								</div>
								<div class="rs-cta">Review candidates →</div>
							</div>
						</a>
					{:else if !pipeRunning}
						<div class="placeholder-card">
							<div class="ph-title">Review candidates</div>
							<div class="ph-body">
								Run the pipeline to fetch and score poster candidates. Once complete, the ranked
								results appear here for approval.
							</div>
						</div>
					{/if}

					{#if runs.length > 0}
						<div class="run-history">
							<div class="rh-head">Recent runs</div>
							{#each runs.slice(0, 6) as r (r.run_id)}
								<a class="rh-row" href={`/pipeline/runs/${r.run_id}`}>
									<StatusDot
										tone={r.status === 'completed' || r.status === 'flagged_manual'
											? 'good'
											: r.status === 'running'
												? 'gold'
												: 'muted'}
										size={6}
									/>
									<span class="rh-id mono">{r.run_id.slice(0, 8)}</span>
									<span class="rh-status">{r.status}</span>
									{#if r.reviewed}<span class="rh-reviewed">reviewed</span>{/if}
									<span class="rh-scorer mono">{r.scorer_name ?? ''}</span>
								</a>
							{/each}
						</div>
					{/if}
				</div>

				<!-- ═══ ACTIVITY TAB ═════════════════════════════════ -->
			{:else if tab === 'activity'}
				<div class="tab-content">
					<div class="activity-empty">
						<Icon name="activity" size={36} stroke={1} />
						<div class="ae-title">Activity feed</div>
						<div class="ae-body">
							Poster deployments, sync events, and pipeline runs for this movie will appear here.
						</div>
					</div>
				</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.crumb {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		color: var(--muted);
		margin-bottom: 18px;
	}
	.crumb a {
		color: var(--muted);
		text-decoration: none;
	}
	.crumb a:hover {
		color: var(--text);
	}
	.crumb span:last-child {
		color: var(--text);
	}

	/* ── Hub layout ──────────────────────────────────────── */
	.hub {
		display: grid;
		grid-template-columns: 220px 1fr;
		gap: 28px;
		align-items: start;
	}
	@media (max-width: 680px) {
		.hub {
			grid-template-columns: 1fr;
		}
		.rail {
			position: static !important;
		}
	}

	/* ── Left rail ───────────────────────────────────────── */
	.rail {
		position: sticky;
		top: calc(var(--header-h) + 16px);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.poster-wrap {
		width: 100%;
	}
	.rail-meta {
		padding: 0 2px;
	}
	.movie-title {
		font-size: 14px;
		font-weight: 650;
		line-height: 1.3;
		color: var(--text);
	}
	.movie-sub {
		font-size: 12px;
		color: var(--muted);
		margin-top: 2px;
	}
	.chips {
		display: flex;
		flex-direction: column;
		gap: 0;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		overflow: hidden;
	}
	.chip {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 7px 10px;
		border-bottom: 1px solid var(--line);
		gap: 8px;
	}
	.chip:last-child {
		border-bottom: none;
	}
	.chip-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
		font-weight: 600;
		white-space: nowrap;
	}
	.chip-val {
		font-size: 12px;
		color: var(--text);
		text-align: right;
	}
	.chip-val.small {
		font-size: 10.5px;
	}
	.chip-status {
		display: flex;
		align-items: center;
		gap: 5px;
		font-size: 12px;
		color: var(--c, var(--text));
	}
	.chip-file .chip-val {
		max-width: 120px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* ── Right pane ──────────────────────────────────────── */
	.pane {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0;
	}
	.tabwrap {
		padding-bottom: 14px;
		border-bottom: 1px solid var(--line);
		margin-bottom: 20px;
	}

	/* ── Tab content shared ──────────────────────────────── */
	.tab-content {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.section-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--faint);
		font-weight: 700;
	}

	/* ── Poster tab ──────────────────────────────────────── */
	.poster-status-row {
		display: flex;
		align-items: center;
		gap: 14px;
		flex-wrap: wrap;
	}
	.status-card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px 14px;
		flex: 1;
		min-width: 160px;
	}
	.sc-label {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 600;
		margin-bottom: 6px;
	}
	.sc-val {
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 14px;
		font-weight: 600;
		color: var(--c, var(--text));
	}
	.profile-card {
		min-width: 0;
	}
	.profile-row {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.profile-select {
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--ink2);
		color: var(--text);
		padding: 8px 10px;
		font-size: 13px;
	}
	.profile-meta {
		display: flex;
		flex-direction: column;
		gap: 3px;
		font-size: 11.5px;
		color: var(--muted);
	}

	/* ── Activity tab ────────────────────────────────────── */
	.activity-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 60px 24px;
		color: var(--faint);
		text-align: center;
	}
	.ae-title {
		font-size: 15px;
		font-weight: 600;
		color: var(--muted);
	}
	.ae-body {
		font-size: 13px;
		color: var(--faint);
		max-width: 340px;
		line-height: 1.5;
	}

	/* ── Pipeline review summary + history ───────────────── */
	.review-summary {
		display: flex;
		gap: 14px;
		align-items: center;
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		text-decoration: none;
	}
	.review-summary:hover {
		border-color: var(--gold);
	}
	.rs-poster {
		width: 54px;
		flex: none;
		aspect-ratio: 2 / 3;
		border-radius: 6px;
		overflow: hidden;
		background: var(--ink2);
	}
	.rs-poster img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.rs-body {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.rs-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
	}
	.rs-counts {
		font-size: 11.5px;
		color: var(--muted);
	}
	.rs-cta {
		font-size: 12px;
		color: var(--gold);
		margin-top: 2px;
	}
	.run-history {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		overflow: hidden;
	}
	.rh-head {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
		padding: 9px 12px;
		border-bottom: 1px solid var(--line);
	}
	.rh-row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 7px 12px;
		border-bottom: 1px solid var(--line);
		text-decoration: none;
		font-size: 12px;
	}
	.rh-row:last-child {
		border-bottom: none;
	}
	.rh-row:hover {
		background: var(--panel2);
	}
	.rh-id {
		color: var(--text);
	}
	.rh-status {
		color: var(--muted);
		text-transform: capitalize;
	}
	.rh-reviewed {
		font-size: 10px;
		color: var(--good);
		border: 1px solid color-mix(in srgb, var(--good) 30%, transparent);
		border-radius: 99px;
		padding: 0 6px;
	}
	.rh-scorer {
		margin-left: auto;
		color: var(--faint);
		font-size: 10.5px;
	}

	/* ── Placeholder cards ───────────────────────────────── */
	.placeholder-card {
		border: 1px dashed var(--line2);
		border-radius: var(--radius-sm);
		padding: 16px 18px;
		background: transparent;
	}
	.ph-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--faint);
		margin-bottom: 4px;
	}
	.ph-body {
		font-size: 12px;
		color: var(--faint);
		line-height: 1.5;
	}

	/* ── Alerts ──────────────────────────────────────────── */
	.alert-box {
		padding: 10px 13px;
		border-radius: var(--radius-sm);
		font-size: 12.5px;
		line-height: 1.4;
		border: 1px solid transparent;
	}
	.alert-box.err {
		background: color-mix(in srgb, var(--bad) 10%, transparent);
		border-color: color-mix(in srgb, var(--bad) 30%, transparent);
		color: var(--bad);
	}
	.info-note {
		font-size: 12px;
		color: var(--faint);
		padding: 6px 0;
	}

	/* ── Error page ──────────────────────────────────────── */
	.errstate {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 10px;
		padding: 80px 24px;
		color: var(--faint);
		text-align: center;
	}
	.errstate strong {
		color: var(--text);
		font-size: 15px;
	}
	.errstate button {
		margin-top: 8px;
		padding: 8px 18px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel);
		color: var(--text);
		font-size: 13px;
	}

	/* ── Buttons ─────────────────────────────────────────── */
	.btn-gold {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-size: 13px;
		font-weight: 600;
		white-space: nowrap;
	}
	.btn-gold:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn-danger {
		padding: 9px 18px;
		border-radius: 8px;
		border: 1px solid var(--bad);
		background: transparent;
		color: var(--bad);
		font-size: 13px;
		font-weight: 600;
		white-space: nowrap;
		cursor: pointer;
		transition:
			background 0.15s,
			color 0.15s;
	}
	.btn-danger:hover {
		background: var(--bad);
		color: var(--ink);
	}
	.btn-danger:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* ── Utilities ───────────────────────────────────────── */
	.mono {
		font-family: var(--font-mono);
	}
	.small {
		font-size: 11px;
	}
	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
	.spin {
		display: inline-block;
		animation: spin 1s linear infinite;
	}
</style>
