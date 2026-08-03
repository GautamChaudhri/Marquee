<script lang="ts">
	import { SvelteMap } from 'svelte/reactivity';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import { getRawDocument } from '$lib/activity/client';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import PosterStatCard from '$lib/components/pipeline/PosterStatCard.svelte';
	import TextProfilePanel from '$lib/components/pipeline/TextProfilePanel.svelte';
	import {
		backupAllPosters,
		clearOcrLabels,
		getPipelineSummary,
		runPosterMaintenance
	} from '$lib/api/pipeline';
	import { getTvSummary } from '$lib/api/pipeline-tv';
	import { resetDeployedPosters } from '$lib/api/config';
	import { runHealScan } from '$lib/api/system';
	import {
		describePlanScope,
		parseSealedPlan,
		type SealedPlan
	} from '$lib/pipeline/maintenance-plan';
	import { bytesH, type Tone } from '$lib/display';
	import { toast } from '$lib/toast';
	import type { PipelineSummary, TvPipelineSummary } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// svelte-ignore state_referenced_locally
	let summary = $state<PipelineSummary>(data.summary);
	// svelte-ignore state_referenced_locally
	let tvSummary = $state<TvPipelineSummary | null>(data.tvSummary);
	let initiatedJobIds = $state<string[]>([]);
	const settledHandlers = new SvelteMap<string, (snapshot: JobSnapshotResponse) => void>();

	let backupBusy = $state(false);
	let resetDialogOpen = $state(false);
	let resetBusy = $state(false);
	let clearOcrDialogOpen = $state(false);
	let clearOcrBusy = $state(false);
	let maintenanceOpen = $state(false);
	let maintenanceBusy = $state(false);
	let maintenancePlan = $state<SealedPlan | null>(null);

	const deployedPct = $derived(
		summary.total_movies ? Math.round((summary.movies_with_poster / summary.total_movies) * 100) : 0
	);
	/** A zeroed stand-in so the television row still renders when the summary call failed. */
	const EMPTY_TV: TvPipelineSummary = {
		shows_total: 0,
		shows_with_show_poster: 0,
		shows_missing_show_poster: 0,
		seasons_total: 0,
		seasons_with_poster: 0,
		seasons_missing_poster: 0,
		shows_fully_covered: 0,
		shows_in_review: 0,
		seasons_in_review: 0,
		assets_in_review: 0,
		assets_in_run: 0,
		shows_no_tmdb: 0,
		running_jobs: [],
		last_heal: null,
		heal_schedule: null,
		backups: { count: 0, bytes: 0 }
	};
	const tv = $derived(tvSummary ?? EMPTY_TV);
	const showArtPct = $derived(
		tv.shows_total ? Math.round((tv.shows_with_show_poster / tv.shows_total) * 100) : 0
	);
	const seasonArtPct = $derived(
		tv.seasons_total ? Math.round((tv.seasons_with_poster / tv.seasons_total) * 100) : 0
	);
	const tvMissing = $derived(tv.shows_missing_show_poster + tv.seasons_missing_poster);

	const coverageTone = (pct: number): Tone => (pct >= 90 ? 'good' : pct >= 70 ? 'warn' : 'bad');

	async function refreshSummary() {
		try {
			[summary, tvSummary] = await Promise.all([
				getPipelineSummary(fetch),
				getTvSummary(fetch).catch(() => tvSummary)
			]);
		} catch {
			/* keep stale cards */
		}
	}

	function trackAction(
		job: { job_id: string },
		label: string,
		onDone?: (job: JobSnapshotResponse) => void
	) {
		initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
		settledHandlers.set(job.job_id, (snapshot) => {
			const succeeded = snapshot.status.outcome === 'succeeded';
			toast(`${label} ${snapshot.status.label.toLowerCase()}`, succeeded ? 'good' : 'bad');
			onDone?.(snapshot);
			void refreshSummary();
		});
	}

	function handleSettled(snapshot: JobSnapshotResponse) {
		settledHandlers.get(snapshot.job_id)?.(snapshot);
		settledHandlers.delete(snapshot.job_id);
	}

	let healBusy = $state(false);

	async function runHeal() {
		healBusy = true;
		try {
			const job = await runHealScan(fetch);
			trackAction(job, 'Heal scan');
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

	async function handleReset() {
		resetBusy = true;
		try {
			const job = await resetDeployedPosters(fetch);
			trackAction(job, 'Poster reset');
			toast('Poster reset queued', 'info');
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not reset posters', 'bad');
		} finally {
			resetBusy = false;
			resetDialogOpen = false;
		}
	}

	async function handleClearOcrLabels() {
		clearOcrBusy = true;
		try {
			const result = await clearOcrLabels(fetch);
			toast(
				`Cleared ${result.deleted_capture_dirs} OCR label capture${result.deleted_capture_dirs === 1 ? '' : 's'}`,
				'good'
			);
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not clear OCR captures', 'bad');
		} finally {
			clearOcrBusy = false;
			clearOcrDialogOpen = false;
		}
	}

	async function previewMaintenance() {
		maintenanceOpen = true;
		maintenanceBusy = true;
		maintenancePlan = null;
		try {
			const job = await runPosterMaintenance(fetch, { dry_run: true });
			// Deliberately not routed through trackAction: a preview is not an
			// outcome worth toasting, and the sealed plan is what we are after.
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			settledHandlers.set(job.job_id, (snapshot) => {
				maintenanceBusy = false;
				if (snapshot.status.outcome !== 'succeeded' && snapshot.status.outcome !== 'no_change') {
					toast('Could not work out what maintenance would remove', 'bad');
					return;
				}
				void (async () => {
					maintenancePlan = parseSealedPlan(await getRawDocument(fetch, job.job_id, 'result'));
					if (!maintenancePlan) toast('Could not read the maintenance plan', 'bad');
				})();
			});
		} catch (e) {
			maintenanceBusy = false;
			toast(e instanceof Error ? e.message : 'Could not start maintenance preview', 'bad');
		}
	}

	async function confirmMaintenance() {
		const plan = maintenancePlan;
		if (!plan || plan.plannedCount === 0) return;
		maintenanceBusy = true;
		try {
			// The checksum from the preview above is what authorizes the delete;
			// without it the handler refuses to mutate.
			const job = await runPosterMaintenance(fetch, {
				dry_run: false,
				confirmed_plan_checksum: plan.planChecksum
			});
			trackAction(job, 'Poster maintenance', () => {
				maintenanceOpen = false;
				maintenanceBusy = false;
				maintenancePlan = null;
			});
		} catch (e) {
			maintenanceBusy = false;
			toast(e instanceof Error ? e.message : 'Could not start maintenance', 'bad');
		}
	}

	function isoDate(value: string | null | undefined): string {
		return value ? new Date(value).toLocaleString() : 'Never';
	}
</script>

<SectionHeader title="Poster Pipeline" subtitle="Manage poster selection and restoration" />

<div class="row-head">
	<span class="eyebrow"><Icon name="film" size={14} /> Movies</span>
	<a class="row-link" href="/pipeline/movies">Open workspace <Icon name="chevron" size={13} /></a>
</div>

<div class="stats" style="--cols:5">
	<PosterStatCard
		label="Movies"
		value={summary.total_movies}
		sub="downloaded"
		tone="info"
		icon="film"
	/>
	<PosterStatCard
		label="Deployed"
		value={summary.movies_with_poster}
		sub={`${deployedPct}% coverage`}
		bar={deployedPct}
		tone={coverageTone(deployedPct)}
		icon="check"
	/>
	<!-- movies_awaiting_run, not movies_missing_poster: the same predicate the
	     workspace Run tab lists, so this count and that list always agree. -->
	<PosterStatCard
		label="Missing"
		value={summary.movies_awaiting_run}
		sub={summary.movies_awaiting_run ? 'awaiting a run' : 'fully covered'}
		tone={summary.movies_awaiting_run ? 'warn' : 'good'}
		icon="alert"
	/>
	<PosterStatCard
		label="In review"
		value={summary.movies_in_review}
		sub="awaiting a decision"
		tone="gold"
		icon="eye"
	/>
	<PosterStatCard
		label="Running"
		value={summary.running_jobs.length || summary.movies_in_run}
		sub={summary.running_jobs.length ? 'active jobs' : 'movies in run'}
		tone="info"
		icon="refresh"
	/>
</div>

<div class="row-head">
	<span class="eyebrow"><Icon name="tv" size={14} /> Television</span>
	<a class="row-link" href="/pipeline/tv">Open workspace <Icon name="chevron" size={13} /></a>
</div>

<div class="stats" style="--cols:6">
	<PosterStatCard
		label="Shows"
		value={tv.shows_total}
		sub={`${tv.shows_fully_covered} fully covered`}
		tone="info"
		icon="tv"
	/>
	<PosterStatCard
		label="Show art"
		value={tv.shows_with_show_poster}
		sub={`${showArtPct}% coverage`}
		bar={showArtPct}
		tone={coverageTone(showArtPct)}
		icon="image"
	/>
	<PosterStatCard
		label="Season art"
		value={tv.seasons_with_poster}
		sub={`${seasonArtPct}% of ${tv.seasons_total} seasons`}
		bar={seasonArtPct}
		tone={coverageTone(seasonArtPct)}
		icon="layers"
	/>
	<PosterStatCard
		label="Missing"
		value={tvMissing}
		breakdown={[
			{ label: 'show', value: tv.shows_missing_show_poster },
			{ label: 'season', value: tv.seasons_missing_poster }
		]}
		tone={tvMissing ? 'warn' : 'good'}
		icon="alert"
	/>
	<PosterStatCard
		label="In review"
		value={tv.assets_in_review}
		breakdown={[
			{ label: 'shows', value: tv.shows_in_review },
			{ label: 'seasons', value: tv.seasons_in_review }
		]}
		tone="gold"
		icon="eye"
	/>
	<PosterStatCard
		label="Running"
		value={tv.running_jobs.length || tv.assets_in_run}
		sub={tv.shows_no_tmdb ? `${tv.shows_no_tmdb} no TMDB match` : 'assets in run'}
		tone={tv.shows_no_tmdb ? 'low' : 'info'}
		icon="refresh"
	/>
</div>

<div class="actions">
	<a class="workspace" href="/pipeline/movies">
		<span class="ws-icon film"><Icon name="film" size={18} /></span>
		<span class="ws-text">
			<b>Movie workspace</b>
			<small>
				{summary.movies_awaiting_run} missing · {summary.movies_in_review} in review
			</small>
		</span>
		<Icon name="chevron" size={16} />
	</a>
	<a class="workspace" href="/pipeline/tv">
		<span class="ws-icon tv"><Icon name="tv" size={18} /></span>
		<span class="ws-text">
			<b>TV workspace</b>
			<small>
				{tv.shows_missing_show_poster} show + {tv.seasons_missing_poster} season missing · {tv.assets_in_review}
				in review
			</small>
		</span>
		<Icon name="chevron" size={16} />
	</a>
</div>

<TextProfilePanel initial={data.textProfiles} />

<FeatureActivityPanel
	scopeKey="feature:pipeline:overview"
	query={{ feature_area: 'ai_posters' }}
	jobIds={initiatedJobIds}
	heading="Poster activity"
	onSettled={handleSettled}
/>

<section class="maintenance-dock">
	<header>
		<div class="maintenance-title">
			<span class="maintenance-icon"><Icon name="settings" size={17} /></span>
			<div>
				<h2>Poster maintenance</h2>
				<p>
					Operational actions stay close to the poster workspace. Configuration now lives in
					Settings.
				</p>
			</div>
		</div>
		<a href="/settings?tab=posters">Poster settings <Icon name="chevron" size={13} /></a>
	</header>

	<div class="maintenance-body">
		<div class="maintenance-facts">
			<div>
				<span>Backups</span><b>{summary.backups.count}</b><small
					>{bytesH(summary.backups.bytes)}</small
				>
			</div>
			<div>
				<span>Last heal</span><b>{isoDate(summary.last_heal?.last_run)}</b><small
					>most recent scan</small
				>
			</div>
			<div>
				<span>Next heal</span><b>{isoDate(summary.heal_schedule?.next_run_at)}</b><small
					>configured schedule</small
				>
			</div>
		</div>
		<div class="maintenance-actions">
			<button onclick={runBackupAll} disabled={backupBusy}
				>{backupBusy ? 'Starting…' : 'Backup all'}</button
			>
			<button onclick={previewMaintenance}>Clean orphaned cache</button>
			<button onclick={runHeal} disabled={healBusy}
				>{healBusy ? 'Running…' : 'Run heal scan'}</button
			>
		</div>
	</div>

	<div class="danger-actions">
		<div>
			<b>Contextual cleanup</b>
			<span>Destructive tools are isolated from configuration and always require confirmation.</span
			>
		</div>
		{#if data.settings?.deployment?.debug}
			<button class="danger-button" onclick={() => (clearOcrDialogOpen = true)}
				>Clear OCR debug captures</button
			>
		{/if}
		<button class="danger-button" onclick={() => (resetDialogOpen = true)}
			>Delete deployed posters</button
		>
	</div>
</section>

<ConfirmDialog
	open={maintenanceOpen}
	title="Poster Maintenance"
	message="Dry-run results are shown before destructive changes run."
	confirmLabel="Run"
	cancelLabel="Close"
	tone="bad"
	busy={maintenanceBusy}
	confirmDisabled={!maintenancePlan || maintenancePlan.plannedCount === 0}
	onConfirm={confirmMaintenance}
	onCancel={() => {
		if (!maintenanceBusy) {
			maintenanceOpen = false;
			maintenancePlan = null;
		}
	}}
>
	<div class="preview" aria-live="polite">
		{#if maintenancePlan && maintenancePlan.plannedCount > 0}
			<span>{describePlanScope(maintenancePlan)} would be removed</span>
		{:else if maintenancePlan}
			<span>No orphaned poster-cache files found</span>
		{:else}
			<span>Preparing preview</span>
		{/if}
	</div>
</ConfirmDialog>

<ConfirmDialog
	open={resetDialogOpen}
	title="Delete all deployed posters?"
	message="This removes deployed movie posters and marks those movies as missing so the pipeline can run again. Cached candidates and taste artifacts are preserved."
	confirmLabel="Delete deployed posters"
	cancelLabel="Cancel"
	tone="bad"
	busy={resetBusy}
	onConfirm={handleReset}
	onCancel={() => (resetDialogOpen = false)}
/>

<ConfirmDialog
	open={clearOcrDialogOpen}
	title="Clear OCR debug captures?"
	message="This deletes debug-only OCR label captures. It does not affect run archives, deployed posters, or taste artifacts."
	confirmLabel="Clear captures"
	cancelLabel="Cancel"
	tone="bad"
	busy={clearOcrBusy}
	onConfirm={handleClearOcrLabels}
	onCancel={() => (clearOcrDialogOpen = false)}
/>

<style>
	.row-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin: 4px 0 8px;
	}
	.eyebrow {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		font-weight: 700;
		color: var(--muted);
	}
	.row-link {
		display: inline-flex;
		align-items: center;
		gap: 3px;
		font-size: 12px;
		color: var(--faint);
	}
	.row-link:hover {
		color: var(--gold);
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(var(--cols, 5), minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 18px;
	}
	.actions {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 18px;
	}
	.workspace {
		min-height: 72px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		color: var(--text);
		padding: 14px 16px;
		display: flex;
		align-items: center;
		gap: 13px;
		font-size: 14px;
		transition:
			border-color 0.15s ease,
			background 0.15s ease;
	}
	.workspace:hover {
		border-color: color-mix(in srgb, var(--gold) 40%, var(--line));
		background: color-mix(in srgb, var(--gold) 5%, var(--panel));
	}
	.ws-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 36px;
		height: 36px;
		flex: none;
		border-radius: 9px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 13%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 26%, transparent);
	}
	.ws-icon.film {
		--c: var(--gold);
	}
	.ws-icon.tv {
		--c: var(--info);
	}
	.ws-text {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 3px;
		min-width: 0;
	}
	.ws-text b {
		font-size: 14px;
		font-weight: 650;
	}
	.ws-text small {
		font-size: 12px;
		color: var(--muted);
	}
	.workspace :global(svg:last-child) {
		color: var(--faint);
		flex: none;
	}
	.maintenance-dock {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		overflow: hidden;
	}
	.maintenance-dock > header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--line);
	}
	.maintenance-title {
		display: flex;
		align-items: center;
		gap: 11px;
	}
	.maintenance-icon {
		display: grid;
		place-items: center;
		width: 34px;
		height: 34px;
		border: 1px solid color-mix(in srgb, var(--gold) 30%, var(--line));
		border-radius: 8px;
		background: var(--gold-soft);
		color: var(--gold);
	}
	.maintenance-title h2 {
		margin: 0;
		font-size: 13px;
		font-weight: 680;
	}
	.maintenance-title p {
		margin: 2px 0 0;
		color: var(--muted);
		font-size: 11.5px;
	}
	.maintenance-dock > header > a {
		display: flex;
		align-items: center;
		gap: 4px;
		color: var(--gold);
		font-size: 11px;
		font-weight: 650;
		white-space: nowrap;
	}
	.maintenance-body {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 18px;
		padding: 15px 16px;
	}
	.maintenance-facts {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 1px;
		border: 1px solid var(--line);
		border-radius: 8px;
		overflow: hidden;
		background: var(--line);
	}
	.maintenance-facts div {
		display: grid;
		gap: 2px;
		min-height: 66px;
		padding: 10px 12px;
		background: var(--panel2);
	}
	.maintenance-facts span {
		color: var(--faint);
		font: 650 9px/1.2 var(--font-mono);
		text-transform: uppercase;
	}
	.maintenance-facts b {
		overflow: hidden;
		color: var(--text);
		font: 600 11px/1.35 var(--font-mono);
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.maintenance-facts small {
		color: var(--muted);
		font-size: 10px;
	}
	.maintenance-actions {
		display: grid;
		grid-template-columns: repeat(3, auto);
		gap: 7px;
	}
	.maintenance-actions button,
	.danger-button {
		border: 1px solid var(--line2);
		border-radius: 7px;
		background: var(--panel2);
		color: var(--text);
		padding: 8px 10px;
		font-size: 11px;
		font-weight: 650;
		white-space: nowrap;
	}
	.maintenance-actions button:disabled,
	.danger-button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.danger-actions {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 11px 16px;
		border-top: 1px solid var(--line);
		background: color-mix(in srgb, var(--bad) 3%, var(--panel));
	}
	.danger-actions > div {
		display: grid;
		gap: 1px;
		margin-right: auto;
	}
	.danger-actions b {
		color: var(--bad);
		font-size: 11px;
	}
	.danger-actions span {
		color: var(--muted);
		font-size: 10.5px;
	}
	.danger-button {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 28%, var(--line));
		background: color-mix(in srgb, var(--bad) 6%, var(--panel2));
	}
	.danger-button:hover {
		background: color-mix(in srgb, var(--bad) 12%, var(--panel2));
	}
	.preview {
		display: grid;
		gap: 6px;
		font-size: 13px;
		color: var(--muted);
	}
	@media (max-width: 1200px) {
		.stats {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}
	@media (max-width: 980px) {
		.stats {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.maintenance-body {
			grid-template-columns: 1fr;
		}
		.maintenance-actions {
			justify-content: end;
		}
	}
	@media (max-width: 680px) {
		.stats,
		.actions {
			grid-template-columns: 1fr;
		}
		.maintenance-dock > header,
		.danger-actions {
			align-items: flex-start;
			flex-direction: column;
		}
		.maintenance-facts {
			grid-template-columns: 1fr;
			width: 100%;
		}
		.maintenance-actions {
			grid-template-columns: 1fr;
		}
		.danger-actions > div {
			margin-right: 0;
		}
	}
</style>
