<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import PosterThumb from '$lib/components/PosterThumb.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import {
		deleteSeasonPoster,
		deleteSeriesPoster,
		getSeasonPosterUrl,
		getSeriesPosterUrl
	} from '$lib/api/library';
	import { runSeries, useShowPoster } from '$lib/api/pipeline-tv';
	import {
		setSeriesTextProfiles,
		type ScopedTextProfileList,
		type SeriesTextProfileSelection
	} from '$lib/api/text-profiles';
	import { posterStatusFromSummary, posterStatusMeta, toneVar } from '$lib/display';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const series = $derived(data.series);
	let running = $state(false);
	let runStatus = $state('running');
	let runDetail = $state<JobProgressDetail>({});
	let deleting = $state<string | null>(null);
	let savingProfiles = $state(false);
	// svelte-ignore state_referenced_locally
	let showProfileId = $state<string | null>(data.seriesProfiles?.show_profile_id ?? null);
	// svelte-ignore state_referenced_locally
	let seasonProfileId = $state<string | null>(data.seriesProfiles?.season_profile_id ?? null);

	const profiles = $derived(data.textProfiles as ScopedTextProfileList | null);
	const showProfiles = $derived(profiles?.scopes.show.profiles ?? []);
	const seasonProfiles = $derived(profiles?.scopes.season.profiles ?? []);
	const seriesProfiles = $derived(data.seriesProfiles as SeriesTextProfileSelection | null);

	function seasonLabel(number: number): string {
		return number === 0 ? 'Specials' : `S${String(number).padStart(2, '0')}`;
	}

	function runLabel(
		mediaType: string | undefined,
		seasonNumber: number | null | undefined
	): string {
		if (mediaType === 'series') return 'Show';
		if (typeof seasonNumber === 'number') return `S${String(seasonNumber).padStart(2, '0')}`;
		return 'Run';
	}

	async function startRun(include: 'all_missing' | 'show' | 'seasons', seasonIds?: number[]) {
		if (!series || running) return;
		running = true;
		runStatus = 'running';
		runDetail = {};
		try {
			const job = await runSeries(fetch, series.id, {
				include,
				season_ids: seasonIds?.length ? seasonIds : undefined
			});
			trackJob(fetch, job.job_id, {
				onProgress: ({ status, detail }) => {
					runStatus = status;
					runDetail = detail;
				},
				onDone: async (snapshot) => {
					running = false;
					toast(`Series run ${snapshot.status}`, snapshot.status === 'succeeded' ? 'good' : 'bad');
					await invalidateAll();
				}
			});
		} catch (e) {
			running = false;
			toast(e instanceof Error ? e.message : 'Failed to start run', 'bad');
		}
	}

	async function removeShowPoster() {
		if (!series) return;
		deleting = 'show';
		try {
			await deleteSeriesPoster(fetch, series.id);
			toast('Show poster deleted', 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to delete show poster', 'bad');
		} finally {
			deleting = null;
		}
	}

	async function removeSeasonPoster(seasonId: number) {
		deleting = `season:${seasonId}`;
		try {
			await deleteSeasonPoster(fetch, seasonId);
			toast('Season poster deleted', 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to delete season poster', 'bad');
		} finally {
			deleting = null;
		}
	}

	async function fallbackToShowPoster(seasonId: number) {
		try {
			await useShowPoster(fetch, seasonId);
			toast('Show poster applied to season', 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to use show poster', 'bad');
		}
	}

	async function saveProfiles() {
		if (!series) return;
		savingProfiles = true;
		try {
			await setSeriesTextProfiles(fetch, series.id, {
				show_profile_id: showProfileId,
				season_profile_id: seasonProfileId
			});
			toast('Series text profiles saved', 'good');
			await invalidateAll();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to save text profiles', 'bad');
		} finally {
			savingProfiles = false;
		}
	}
</script>

{#if data.error || !series}
	<div class="state error">
		<strong>Couldn't load this series.</strong>
		<span>{data.error ?? 'Unknown error'}</span>
	</div>
{:else}
	<SectionHeader
		title={series.title}
		subtitle={`${series.year ?? '—'} · ${series.downloaded_seasons} downloaded seasons`}
	/>

	<div class="layout">
		<aside class="rail">
			<PosterThumb
				title={series.title}
				year={series.year}
				posterStatus={posterStatusFromSummary(series.poster)}
				posterUrl={series.poster.has_poster ? getSeriesPosterUrl(series.id) : null}
			/>
			<div class="chips">
				<div class="chip">
					<span class="label">TMDB</span>
					<span class="mono">{series.tmdb_id ?? '—'}</span>
				</div>
				<div class="chip">
					<span class="label">TVDB</span>
					<span class="mono">{series.tvdb_id ?? '—'}</span>
				</div>
				<div class="chip">
					<span class="label">Poster</span>
					<span
						class="status"
						style={`--c:${toneVar(posterStatusMeta[posterStatusFromSummary(series.poster)].tone)}`}
					>
						<StatusDot
							tone={posterStatusMeta[posterStatusFromSummary(series.poster)].tone}
							size={6}
						/>
						{posterStatusMeta[posterStatusFromSummary(series.poster)].label}
					</span>
				</div>
			</div>
			<div class="actions">
				<button class="btn-gold" onclick={() => startRun('all_missing')} disabled={running}>
					Run all missing
				</button>
				<button class="btn-sec" onclick={() => startRun('show')} disabled={running}>
					Run show only
				</button>
				{#if series.poster.has_poster}
					<button class="btn-danger" onclick={removeShowPoster} disabled={deleting === 'show'}>
						Delete show poster
					</button>
				{/if}
			</div>
			{#if running}
				<RunProgress detail={runDetail} status={runStatus} title="Running TV pipeline" />
			{/if}
		</aside>

		<div class="pane">
			<section class="card">
				<h3>Season posters</h3>
				<div class="season-grid">
					{#each series.seasons as season (season.id)}
						<div class="season-card">
							<div class="season-head">
								<strong>{seasonLabel(season.season_number)}</strong>
								<span class="mono">{season.episode_file_count ?? 0} eps</span>
							</div>
							<PosterThumb
								title={`${series.title} ${seasonLabel(season.season_number)}`}
								posterStatus={posterStatusFromSummary(season.poster)}
								posterUrl={season.poster.has_poster ? getSeasonPosterUrl(season.id) : null}
							/>
							<div class="season-actions">
								<button class="btn-ghost" onclick={() => goto(`/pipeline/tv/series/${series.id}`)}>
									View review
								</button>
								<button
									class="btn-ghost"
									onclick={() => startRun('seasons', [season.id])}
									disabled={running}
								>
									Run
								</button>
								{#if season.poster.has_poster}
									<button
										class="btn-danger small"
										onclick={() => removeSeasonPoster(season.id)}
										disabled={deleting === `season:${season.id}`}
									>
										Delete
									</button>
								{/if}
								{#if series.poster.has_poster && !season.poster.has_poster}
									<button class="btn-sec small" onclick={() => fallbackToShowPoster(season.id)}>
										Use show poster
									</button>
								{/if}
							</div>
						</div>
					{/each}
				</div>
			</section>

			{#if profiles && seriesProfiles}
				<section class="card">
					<h3>Text profile overrides</h3>
					<div class="profile-grid">
						<label>
							<span>Show profile</span>
							<select bind:value={showProfileId}>
								<option value={null}>Use default ({seriesProfiles.effective_show.name})</option>
								{#each showProfiles as profile (profile.id)}
									<option value={profile.id}>{profile.name}</option>
								{/each}
							</select>
						</label>
						<label>
							<span>Season profile</span>
							<select bind:value={seasonProfileId}>
								<option value={null}>Use default ({seriesProfiles.effective_season.name})</option>
								{#each seasonProfiles as profile (profile.id)}
									<option value={profile.id}>{profile.name}</option>
								{/each}
							</select>
						</label>
					</div>
					<button class="btn-sec" onclick={saveProfiles} disabled={savingProfiles}>
						Save overrides
					</button>
				</section>
			{/if}

			<section class="card">
				<h3>Run history</h3>
				<div class="list">
					{#each data.runs as run (run.run_id)}
						<a
							class="row"
							href={run.media_type === 'series' || run.media_type === 'season'
								? `/pipeline/tv/series/${series.id}`
								: `/pipeline/runs/${run.run_id}`}
						>
							<span>{runLabel(run.media_type, run.season_number)}</span>
							<span class="mono">{run.run_id.slice(0, 8)}</span>
							<span>{run.status}</span>
							<span>{run.reviewed ? 'Reviewed' : 'Pending'}</span>
						</a>
					{:else}
						<div class="empty">No runs yet.</div>
					{/each}
				</div>
			</section>

			<section class="card">
				<h3>Artwork events</h3>
				<div class="list">
					{#each data.events as event (event.id)}
						<div class="row">
							<span
								>{event.media_type === 'series'
									? 'Show'
									: event.season_id
										? `Season ${event.season_id}`
										: 'Season'}</span
							>
							<span>{event.action}</span>
							<span>{event.source}</span>
							<span>{event.created_at ? new Date(event.created_at).toLocaleString() : '—'}</span>
						</div>
					{:else}
						<div class="empty">No artwork events yet.</div>
					{/each}
				</div>
			</section>
		</div>
	</div>
{/if}

<style>
	.state {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 40px 24px;
		text-align: center;
		background: var(--panel);
		color: var(--muted);
	}
	.layout {
		display: grid;
		grid-template-columns: 280px minmax(0, 1fr);
		gap: 20px;
	}
	@media (max-width: 900px) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
	.rail,
	.card {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
		padding: 16px;
	}
	.rail {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.pane {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.chips,
	.actions,
	.profile-grid,
	.list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.chip {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
	}
	.label {
		color: var(--muted);
	}
	.status {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: var(--c);
	}
	.season-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
		gap: 14px;
	}
	.season-card {
		display: flex;
		flex-direction: column;
		gap: 10px;
		border: 1px solid var(--line);
		border-radius: 12px;
		padding: 12px;
		background: var(--panel2);
	}
	.season-head,
	.row {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		align-items: center;
	}
	.season-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	h3 {
		margin: 0 0 12px;
		font-size: 15px;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 6px;
		font-size: 12px;
		color: var(--muted);
	}
	select {
		padding: 9px 12px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		font-size: 13px;
	}
	.btn-gold,
	.btn-sec,
	.btn-danger,
	.btn-ghost {
		padding: 9px 12px;
		border-radius: 8px;
		font-size: 13px;
	}
	.btn-gold {
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
	}
	.btn-sec {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
	}
	.btn-ghost {
		border: 1px solid var(--line2);
		background: transparent;
		color: var(--muted);
	}
	.btn-danger {
		border: 1px solid color-mix(in srgb, var(--bad) 50%, transparent);
		background: color-mix(in srgb, var(--bad) 10%, var(--panel2));
		color: var(--bad);
	}
	.small {
		padding: 7px 10px;
		font-size: 12px;
	}
	.empty {
		color: var(--muted);
		font-size: 13px;
	}
</style>
