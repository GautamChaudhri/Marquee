<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import TextProfilePanel from '$lib/components/pipeline/TextProfilePanel.svelte';
	import {
		backupAllPosters,
		getPipelineSummary,
		rescanPosters,
		runPosterMaintenance
	} from '$lib/api/pipeline';
	import { getTvSummary } from '$lib/api/pipeline-tv';
	import { getSettings, putSettings, runHealScan } from '$lib/api/system';
	import { CONFIGURATION_CONFLICT_MESSAGE, isConfigurationConflict } from '$lib/api/client';
	import type { JobSnapshot } from '$lib/api/jobs';
	import { bytesH } from '$lib/display';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import type {
		PipelineSummary,
		RuntimeSettings,
		SummaryRunningJob,
		TvPipelineSummary
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	type Preset = 'movie' | 'poster' | 'custom';
	type RunningDisplay = SummaryRunningJob & { detail: JobProgressDetail; status: string };

	// svelte-ignore state_referenced_locally
	let summary = $state<PipelineSummary>(data.summary);
	// svelte-ignore state_referenced_locally
	let tvSummary = $state<TvPipelineSummary | null>(data.tvSummary);
	// svelte-ignore state_referenced_locally
	let runtimeSettings = $state<RuntimeSettings | null>(data.settings);
	// svelte-ignore state_referenced_locally
	let runningJobs = $state<RunningDisplay[]>(
		summary.running_jobs.map((job) => ({
			...job,
			status: job.status,
			detail: (job.progress ?? {}) as JobProgressDetail
		}))
	);

	let savingPoster = $state(false);
	let savingRestore = $state(false);
	let savingHeal = $state(false);
	let backupBusy = $state(false);
	let maintenanceOpen = $state(false);
	let maintenanceBusy = $state(false);
	let maintenancePreview = $state<Record<string, unknown> | null>(null);

	const currentMovieFormat = $derived(
		String(runtimeSettings?.poster_formats?.movie ?? 'poster.jpg')
	);
	const currentShowFormat = $derived(String(runtimeSettings?.poster_formats?.series ?? 'show.jpg'));
	const currentSeasonFormat = $derived(
		String(runtimeSettings?.poster_formats?.season ?? 'season{season:02d}.jpg')
	);
	const currentRestoreMethod = $derived(
		(runtimeSettings?.posters?.restore_method as 'download' | 'local' | undefined) ?? 'download'
	);
	const currentHealEnabled = $derived(Boolean(runtimeSettings?.sync?.heal_enabled ?? true));
	const currentHealInterval = $derived(Number(runtimeSettings?.sync?.heal_interval_minutes ?? 60));

	// svelte-ignore state_referenced_locally
	let preset = $state<Preset>(formatToPreset(currentMovieFormat));
	// svelte-ignore state_referenced_locally
	let customName = $state(formatToCustom(currentMovieFormat));
	// svelte-ignore state_referenced_locally
	let showName = $state(currentShowFormat.replace(/\.jpe?g$/i, ''));
	// svelte-ignore state_referenced_locally
	let seasonTemplate = $state(currentSeasonFormat);
	// svelte-ignore state_referenced_locally
	let restoreMethod = $state<'download' | 'local'>(currentRestoreMethod);
	// svelte-ignore state_referenced_locally
	let healEnabled = $state(currentHealEnabled);
	// svelte-ignore state_referenced_locally
	let healInterval = $state(currentHealInterval);

	const deployedPct = $derived(
		summary.total_movies ? Math.round((summary.movies_with_poster / summary.total_movies) * 100) : 0
	);
	const posterDirty = $derived(
		nextMovieFormat() !== currentMovieFormat ||
			nextShowFormat() !== currentShowFormat ||
			nextSeasonFormat() !== currentSeasonFormat
	);
	const restoreDirty = $derived(restoreMethod !== currentRestoreMethod);
	const healDirty = $derived(
		healEnabled !== currentHealEnabled || Number(healInterval) !== currentHealInterval
	);

	let stops: (() => void)[] = [];

	function formatToPreset(format: string): Preset {
		if (format === '{movie_basename}.jpg') return 'movie';
		if (format === 'poster.jpg') return 'poster';
		return 'custom';
	}

	function formatToCustom(format: string): string {
		return format.replaceAll('{movie_basename}', '<base_filename>').replace(/\.jpe?g$/i, '');
	}

	function nextMovieFormat(): string {
		if (preset === 'movie') return '{movie_basename}.jpg';
		if (preset === 'poster') return 'poster.jpg';
		const base = (customName || 'poster')
			.trim()
			.replaceAll('{movie_basename}', '<base_filename>')
			.replace(/\.jpe?g$/i, '');
		return `${base.replaceAll('<base_filename>', '{movie_basename}')}.jpg`;
	}

	function nextShowFormat(): string {
		const base = showName.trim().replace(/\.jpe?g$/i, '') || 'show';
		return `${base}.jpg`;
	}

	function nextSeasonFormat(): string {
		return seasonTemplate.trim() || 'season{season:02d}.jpg';
	}

	function resetFormsFromSettings() {
		preset = formatToPreset(currentMovieFormat);
		customName = formatToCustom(currentMovieFormat);
		showName = currentShowFormat.replace(/\.jpe?g$/i, '');
		seasonTemplate = currentSeasonFormat;
		restoreMethod = currentRestoreMethod;
		healEnabled = currentHealEnabled;
		healInterval = currentHealInterval;
	}

	async function handleConfigurationSaveError(error: unknown, fallback: string) {
		if (isConfigurationConflict(error)) {
			runtimeSettings = await getSettings(fetch);
			toast(CONFIGURATION_CONFLICT_MESSAGE, 'info');
			return;
		}
		toast(error instanceof Error ? error.message : fallback, 'bad');
	}

	async function refreshSummary() {
		try {
			[summary, tvSummary] = await Promise.all([
				getPipelineSummary(fetch),
				getTvSummary(fetch).catch(() => tvSummary)
			]);
			runningJobs = summary.running_jobs.map((job) => ({
				...job,
				status: job.status,
				detail: (job.progress ?? {}) as JobProgressDetail
			}));
			trackRunningJobs();
		} catch {
			/* keep stale cards */
		}
	}

	function stopTracking() {
		for (const stop of stops) stop();
		stops = [];
	}

	function trackRunningJobs() {
		stopTracking();
		for (const job of runningJobs) {
			const stop = trackJob(
				fetch,
				job.job_id,
				{
					onProgress: ({ status, detail }) => {
						runningJobs = runningJobs.map((item) =>
							item.job_id === job.job_id ? { ...item, status, detail } : item
						);
					},
					onDone: (done) => {
						toast(
							`${done.label ?? job.label ?? 'Job'} ${done.status}`,
							done.status === 'succeeded' ? 'good' : 'bad'
						);
						void refreshSummary();
					}
				},
				{ eventsUrl: job.events_url }
			);
			stops.push(stop);
		}
	}

	function trackAction(
		job: { job_id: string; events_url?: string },
		label: string,
		onDone?: (job: JobSnapshot) => void
	) {
		const stop = trackJob(
			fetch,
			job.job_id,
			{
				onDone: (done) => {
					toast(`${label} ${done.status}`, done.status === 'succeeded' ? 'good' : 'bad');
					onDone?.(done);
					void refreshSummary();
				}
			},
			{ eventsUrl: job.events_url }
		);
		stops.push(stop);
	}

	async function savePosterFormat() {
		savingPoster = true;
		try {
			const result = await putSettings(
				fetch,
				{
					posters: {
						movie_poster_format: nextMovieFormat(),
						series_poster_format: nextShowFormat(),
						season_poster_format: nextSeasonFormat()
					}
				},
				runtimeSettings!.configuration_version
			);
			runtimeSettings = result.settings;
			resetFormsFromSettings();
			const job = await rescanPosters(fetch);
			trackAction(job, 'Poster rescan', (done) => {
				const result = (done.result ?? {}) as Record<string, unknown>;
				const updated = Number(result.changed ?? 0);
				const missing = Number(result.missing ?? 0);
				toast(`${updated} updated, ${missing} missing`, missing ? 'info' : 'good');
			});
		} catch (e) {
			await handleConfigurationSaveError(e, 'Could not save poster filename');
		} finally {
			savingPoster = false;
		}
	}

	async function saveRestoreMethod() {
		savingRestore = true;
		try {
			const result = await putSettings(
				fetch,
				{ posters: { restore_method: restoreMethod } },
				runtimeSettings!.configuration_version
			);
			runtimeSettings = result.settings;
			resetFormsFromSettings();
			toast('Restoration saved', 'good');
		} catch (e) {
			await handleConfigurationSaveError(e, 'Could not save restoration method');
		} finally {
			savingRestore = false;
		}
	}

	async function saveHeal() {
		savingHeal = true;
		try {
			const result = await putSettings(
				fetch,
				{ heal: { enabled: healEnabled, interval_minutes: Number(healInterval) } },
				runtimeSettings!.configuration_version
			);
			runtimeSettings = result.settings;
			resetFormsFromSettings();
			toast('Heal scan saved', 'good');
			await refreshSummary();
		} catch (e) {
			await handleConfigurationSaveError(e, 'Could not save heal scan');
		} finally {
			savingHeal = false;
		}
	}

	let healBusy = $state(false);

	async function runHeal() {
		healBusy = true;
		try {
			const job = await runHealScan(fetch);
			trackAction(job, 'Heal scan', (done) => {
				const result = (done.result ?? {}) as Record<string, unknown>;
				const checked = Number(result.checked ?? 0);
				const restored = Number(result.restored ?? 0);
				const failed = Number(result.failed ?? 0);
				toast(
					`Heal scan complete: ${checked} checked, ${restored} restored, ${failed} failed`,
					restored ? 'good' : 'info'
				);
			});
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not start heal scan', 'bad');
		} finally {
			healBusy = false;
		}
	}

	async function runBackupAll() {
		backupBusy = true;
		try {
			const job = await backupAllPosters(fetch);
			trackAction(job, 'Poster backup');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not start backup', 'bad');
		} finally {
			backupBusy = false;
		}
	}

	async function previewMaintenance() {
		maintenanceOpen = true;
		maintenanceBusy = true;
		maintenancePreview = null;
		try {
			const job = await runPosterMaintenance(fetch, { dry_run: true });
			trackAction(job, 'Maintenance preview', (done) => {
				maintenancePreview = (done.result ?? {}) as Record<string, unknown>;
				maintenanceBusy = false;
			});
		} catch (e) {
			maintenanceBusy = false;
			toast(e instanceof Error ? e.message : 'Could not start maintenance preview', 'bad');
		}
	}

	async function confirmMaintenance() {
		maintenanceBusy = true;
		try {
			const job = await runPosterMaintenance(fetch, { dry_run: false });
			trackAction(job, 'Poster maintenance', () => {
				maintenanceOpen = false;
				maintenanceBusy = false;
			});
		} catch (e) {
			maintenanceBusy = false;
			toast(e instanceof Error ? e.message : 'Could not start maintenance', 'bad');
		}
	}

	function isoDate(value: string | null | undefined): string {
		return value ? new Date(value).toLocaleString() : 'Never';
	}

	onMount(() => {
		trackRunningJobs();
	});

	onDestroy(() => {
		stopTracking();
	});
</script>

<SectionHeader title="Poster Pipeline" subtitle="Manage poster selection and restoration" />

<div class="stats">
	<StatCard label="Movies" value={summary.total_movies} sub="downloaded" tone="info" />
	<StatCard
		label="Deployed"
		value={summary.movies_with_poster}
		sub={`${deployedPct}% coverage`}
		bar={deployedPct}
		tone={deployedPct >= 90 ? 'good' : deployedPct >= 70 ? 'warn' : 'bad'}
	/>
	<StatCard
		label="Missing"
		value={summary.movies_missing_poster}
		tone={summary.movies_missing_poster ? 'warn' : 'good'}
	/>
	<StatCard label="In review" value={summary.movies_in_review} tone="gold" />
	<StatCard
		label="Running"
		value={summary.running_jobs.length || summary.movies_in_run}
		tone="info"
	/>
</div>

<div class="actions">
	<button class="action" onclick={() => goto('/pipeline/movies')}>
		<span>Movie posters</span>
		<b>{summary.movies_missing_poster}</b>
	</button>
	<button class="action" onclick={() => goto('/pipeline/tv')}>
		<span>TV posters</span>
		<b
			>{tvSummary
				? `${tvSummary.shows_missing_show_poster + tvSummary.seasons_missing_poster} missing`
				: 'Open'}</b
		>
	</button>
</div>

<TextProfilePanel initial={data.textProfiles} />

{#if runningJobs.length}
	<section class="band">
		<h2>Running Jobs</h2>
		<div class="runs">
			{#each runningJobs as job (job.job_id)}
				<RunProgress title={job.label ?? job.type} status={job.status} detail={job.detail} />
			{/each}
		</div>
	</section>
{/if}

<div class="settings-grid">
	<details class="panel" open>
		<summary>Poster Filename</summary>
		<div class="panel-body">
			<div class="section-title">Movie</div>
			<label class="radio">
				<input type="radio" bind:group={preset} value="movie" />
				<span>Movie filename</span>
				<small>{'{movie_basename}.jpg'}</small>
			</label>
			<label class="radio">
				<input type="radio" bind:group={preset} value="poster" />
				<span>Poster</span>
				<small>poster.jpg</small>
			</label>
			<label class="radio">
				<input type="radio" bind:group={preset} value="custom" />
				<span>Custom</span>
				<input class="inline-input" bind:value={customName} disabled={preset !== 'custom'} />
			</label>
			<div class="section-title">Show</div>
			<label class="field">
				<span>Filename</span>
				<input class="inline-input" bind:value={showName} />
			</label>
			<div class="section-title">Season</div>
			<label class="field field-col">
				<span>Template</span>
				<input class="inline-input" bind:value={seasonTemplate} />
				<small
					>Preview: {nextSeasonFormat()
						.replace('{season:02d}', '01')
						.replace('{season}', '1')}</small
				>
			</label>
			<div class="panel-foot">
				<code>{nextMovieFormat()} · {nextShowFormat()} · {nextSeasonFormat()}</code>
				<button onclick={savePosterFormat} disabled={!posterDirty || savingPoster}>
					{savingPoster ? 'Saving' : 'Save'}
				</button>
			</div>
		</div>
	</details>

	<details class="panel" open>
		<summary>Restoration</summary>
		<div class="panel-body">
			<label class="radio">
				<input type="radio" bind:group={restoreMethod} value="download" />
				<span>Download</span>
				<small>cache, download, local</small>
			</label>
			<label class="radio">
				<input type="radio" bind:group={restoreMethod} value="local" />
				<span>Local</span>
				<small>local, cache, download</small>
			</label>
			<div class="backup-row">
				<span>{summary.backups.count} backups</span>
				<span>{bytesH(summary.backups.bytes)}</span>
			</div>
			<div class="panel-foot">
				<button onclick={runBackupAll} disabled={backupBusy}>
					{backupBusy ? 'Starting' : 'Backup all'}
				</button>
				<button onclick={previewMaintenance}>Run maintenance</button>
				<button onclick={saveRestoreMethod} disabled={!restoreDirty || savingRestore}>
					{savingRestore ? 'Saving' : 'Save'}
				</button>
			</div>
		</div>
	</details>

	<details class="panel" open>
		<summary>Heal Scan</summary>
		<div class="panel-body">
			<label class="toggle">
				<input type="checkbox" bind:checked={healEnabled} />
				<span>{healEnabled ? 'Enabled' : 'Disabled'}</span>
			</label>
			<label class="field">
				<span>Interval</span>
				<select bind:value={healInterval}>
					{#each [15, 30, 60, 120, 180, 360, 720, 1440] as minutes (minutes)}
						<option value={minutes}>{minutes} min</option>
					{/each}
				</select>
			</label>
			<div class="facts">
				<span>Last: {isoDate(summary.last_heal?.last_run)}</span>
				<span>Next: {isoDate(summary.heal_schedule?.next_run_at)}</span>
			</div>
			<div class="panel-foot">
				<button onclick={runHeal} disabled={healBusy}>
					{healBusy ? 'Running…' : 'Run scan'}
				</button>
				<button onclick={saveHeal} disabled={!healDirty || savingHeal}>
					{savingHeal ? 'Saving' : 'Save'}
				</button>
			</div>
		</div>
	</details>
</div>

<ConfirmDialog
	open={maintenanceOpen}
	title="Poster Maintenance"
	message="Dry-run results are shown before destructive changes run."
	confirmLabel="Run"
	cancelLabel="Close"
	tone="bad"
	busy={maintenanceBusy}
	confirmDisabled={!maintenancePreview}
	onConfirm={confirmMaintenance}
	onCancel={() => {
		if (!maintenanceBusy) maintenanceOpen = false;
	}}
>
	{#if maintenancePreview}
		<div class="preview">
			<span>Movies: {maintenancePreview.movies_deleted ?? 0}</span>
			<span>Backups: {maintenancePreview.orphan_backups ?? 0}</span>
			<span>Cache: {maintenancePreview.orphan_cache ?? 0}</span>
		</div>
	{:else}
		<div class="preview">Preparing preview</div>
	{/if}
</ConfirmDialog>

<style>
	.stats {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 16px;
	}
	.actions {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 18px;
	}
	.action {
		min-height: 72px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--text);
		padding: 14px 16px;
		display: flex;
		align-items: center;
		justify-content: space-between;
		font-size: 14px;
	}
	.action b {
		font-family: var(--font-mono);
		color: var(--gold);
		font-size: 13px;
	}
	.band {
		margin-bottom: 18px;
	}
	h2 {
		font-size: 14px;
		margin: 0 0 10px;
	}
	.runs {
		display: grid;
		gap: 10px;
	}
	.settings-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 12px;
	}
	.panel {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	summary {
		cursor: pointer;
		padding: 13px 15px;
		font-size: 13px;
		font-weight: 650;
		color: var(--text);
		border-bottom: 1px solid var(--line);
	}
	.panel-body {
		padding: 14px 15px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.radio,
	.toggle,
	.field,
	.backup-row,
	.facts {
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 13px;
		color: var(--text);
	}
	.radio small {
		margin-left: auto;
		color: var(--muted);
		font-family: var(--font-mono);
		font-size: 11px;
	}
	.inline-input,
	select {
		min-width: 0;
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--ink2);
		color: var(--text);
		padding: 7px 9px;
		font-size: 13px;
	}
	.inline-input {
		flex: 1;
	}
	.field {
		justify-content: space-between;
	}
	.field-col {
		flex-direction: column;
		align-items: flex-start;
	}
	.section-title {
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--faint);
	}
	.backup-row,
	.facts {
		justify-content: space-between;
		color: var(--muted);
	}
	.facts {
		flex-direction: column;
		align-items: flex-start;
		gap: 5px;
	}
	.panel-foot {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 8px;
		border-top: 1px solid var(--line);
		padding-top: 12px;
	}
	code {
		margin-right: auto;
		color: var(--muted);
		font-size: 11px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	button {
		border: 1px solid var(--line2);
		border-radius: 8px;
		background: var(--panel2);
		color: var(--text);
		padding: 8px 12px;
		font-size: 13px;
	}
	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.preview {
		display: grid;
		gap: 6px;
		font-size: 13px;
		color: var(--muted);
	}
	@media (max-width: 980px) {
		.stats,
		.settings-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@media (max-width: 680px) {
		.stats,
		.actions,
		.settings-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
