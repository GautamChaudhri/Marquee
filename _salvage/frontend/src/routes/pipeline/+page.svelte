<script lang="ts">
	import { SvelteMap } from 'svelte/reactivity';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import { getRawDocument } from '$lib/activity/client';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import SectionDivider from '$lib/components/SectionDivider.svelte';
	import CandidateFunnel from '$lib/components/pipeline/CandidateFunnel.svelte';
	import OcrExecutionPanel from '$lib/components/pipeline/OcrExecutionPanel.svelte';
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
	/** Show art and season art rolled into one "Deployed" figure, so the television
	 *  row has the same five columns as the film row. */
	const tvDeployed = $derived(tv.shows_with_show_poster + tv.seasons_with_poster);
	const tvArtTotal = $derived(tv.shows_total + tv.seasons_total);
	const tvArtPct = $derived(tvArtTotal ? Math.round((tvDeployed / tvArtTotal) * 100) : 0);

	/* Exact counts rather than the rounded percentage: 99.6% coverage displays as 100
	   and would put a tick on a row that still has work left in it. */
	const filmsFullyDeployed = $derived(
		summary.total_movies > 0 && summary.movies_with_poster === summary.total_movies
	);
	const tvFullyDeployed = $derived(tvArtTotal > 0 && tvDeployed === tvArtTotal);

	const coverageTone = (pct: number): Tone => (pct >= 90 ? 'good' : pct >= 70 ? 'warn' : 'bad');

	/** Only a card with work behind it earns the loud treatment — a grid where
	 *  everything shouts tells you nothing about where to look. */
	const emphasisFor = (count: number): 'loud' | 'quiet' => (count > 0 ? 'loud' : 'quiet');

	const outstanding = $derived(
		summary.movies_awaiting_run + summary.movies_in_review + tvMissing + tv.assets_in_review
	);
	const coverageNote = $derived(
		outstanding ? `${outstanding} awaiting attention` : 'everything covered'
	);

	/** Named on the funnel's text-gate row so the rule sits beside what it removed. */
	const activeMovieProfile = $derived.by(() => {
		const scope = data.textProfiles?.scopes.movie;
		if (!scope) return undefined;
		return scope.profiles.find((profile) => profile.id === scope.default_id)?.name;
	});

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

<!-- Both rows wear the same head: what the row covers, anything blocking it, and the
     way into its workspace. No figures live here — every number on this screen is
     stated once, by the card that owns it. -->
{#snippet rowHead(row: {
	icon: string;
	title: string;
	href: string;
	linkLabel: string;
	warn?: { text: string; href: string };
})}
	<div class="row-head">
		<span class="eyebrow"><Icon name={row.icon} size={14} /> {row.title}</span>
		{#if row.warn}
			<a class="row-warn" href={row.warn.href}>
				<Icon name="alert" size={11} />
				{row.warn.text}
			</a>
		{/if}
		<a class="row-link workspace-link" href={row.href}>
			{row.linkLabel}
			<Icon name="chevron" size={13} />
		</a>
	</div>
{/snippet}

<SectionDivider label="Coverage" note={coverageNote} />

{@render rowHead({
	icon: 'film',
	title: 'Films',
	href: '/pipeline/movies',
	linkLabel: 'Open Film Workspace'
})}

<div class="stats" style="--cols:4">
	<!-- Labelled "Assets" in both rows: a film needs one poster, a show or season one
	     piece of artwork, so the two rows count the same unit and can be compared. -->
	<PosterStatCard
		label="Assets"
		value={summary.total_movies}
		sub="in library"
		tone="purple"
		icon="film"
	/>
	<!-- Deployed is always green and Missing always red: the tone names which metric
	     you are reading, so the row can be scanned by colour. How healthy the number
	     is comes from the meter and the dimming of a zero, not from recolouring.
	     The icon is the exception that earns its place — a tick only once the row is
	     genuinely complete, an alert or an eye only while there is something to do. -->
	<PosterStatCard
		label="Deployed"
		value={summary.movies_with_poster}
		sub={`${deployedPct}% coverage`}
		bar={deployedPct}
		barTone={coverageTone(deployedPct)}
		tone="good"
		icon={filmsFullyDeployed ? 'check' : undefined}
	/>
	<!-- movies_awaiting_run, not movies_missing_poster: the same predicate the
	     workspace Run tab lists, so this count and that list always agree. -->
	<PosterStatCard
		label="Missing"
		value={summary.movies_awaiting_run}
		sub={summary.movies_awaiting_run ? 'awaiting a run' : 'fully covered'}
		tone="bad"
		icon={summary.movies_awaiting_run ? 'alert' : undefined}
		href="/pipeline/movies?tab=run"
		hint={`${summary.movies_awaiting_run} films missing a poster — open the Run queue`}
		emphasis={emphasisFor(summary.movies_awaiting_run)}
	/>
	<PosterStatCard
		label="In Review"
		value={summary.movies_in_review}
		sub="awaiting a decision"
		tone="gold"
		icon={summary.movies_in_review ? 'eye' : undefined}
		href="/pipeline/movies?tab=review"
		hint={`${summary.movies_in_review} films awaiting a decision — open the Review queue`}
		emphasis={emphasisFor(summary.movies_in_review)}
	/>
</div>

{@render rowHead({
	icon: 'tv',
	title: 'Television',
	href: '/pipeline/tv',
	linkLabel: 'Open Television Workspace',
	warn: tv.shows_no_tmdb
		? { text: `${tv.shows_no_tmdb} without a TMDB match`, href: '/pipeline/tv?tab=run' }
		: undefined
})}

<!-- Every television card counts the same unit — an asset is one show's artwork or
     one season's — and carries the show/season split beneath the combined figure,
     so the five cards share one denominator and can be read against each other. -->
<div class="stats" style="--cols:4">
	<PosterStatCard
		label="Assets"
		value={tvArtTotal}
		sub="in library"
		split={[
			{ label: 'Shows', value: tv.shows_total },
			{ label: 'Seasons', value: tv.seasons_total }
		]}
		tone="purple"
		icon="tv"
	/>
	<!-- The one card where the split is a proportion rather than a count, so it keeps
	     its meters — reading out how many are deployed, with the fill carrying how far
	     that is through the shows and seasons the row has. -->
	<PosterStatCard
		label="Deployed"
		value={tvDeployed}
		sub={`${tvArtPct}% coverage`}
		bars={[
			{
				label: 'Shows',
				value: showArtPct,
				display: tv.shows_with_show_poster,
				tone: coverageTone(showArtPct)
			},
			{
				label: 'Seasons',
				value: seasonArtPct,
				display: tv.seasons_with_poster,
				tone: coverageTone(seasonArtPct)
			}
		]}
		tone="good"
		icon={tvFullyDeployed ? 'check' : undefined}
	/>
	<PosterStatCard
		label="Missing"
		value={tvMissing}
		sub={tvMissing ? 'awaiting a run' : 'fully covered'}
		split={[
			{ label: 'Shows', value: tv.shows_missing_show_poster },
			{ label: 'Seasons', value: tv.seasons_missing_poster }
		]}
		tone="bad"
		icon={tvMissing ? 'alert' : undefined}
		href="/pipeline/tv?tab=run"
		hint={`${tvMissing} television assets missing artwork — open the Run queue`}
		emphasis={emphasisFor(tvMissing)}
	/>
	<PosterStatCard
		label="In Review"
		value={tv.assets_in_review}
		sub="awaiting a decision"
		split={[
			{ label: 'Shows', value: tv.shows_in_review },
			{ label: 'Seasons', value: tv.seasons_in_review }
		]}
		tone="gold"
		icon={tv.assets_in_review ? 'eye' : undefined}
		href="/pipeline/tv?tab=review"
		hint={`${tv.assets_in_review} television assets awaiting a decision — open the Review queue`}
		emphasis={emphasisFor(tv.assets_in_review)}
	/>
</div>

{#if data.ocr && data.settings}
	<!-- Labelled "Hardware", not "Execution": the card below is already called OCR
	     Execution and carries its own one-line explanation. -->
	<SectionDivider label="Hardware" />

	<OcrExecutionPanel
		initial={data.ocr}
		configVersion={data.settings.configuration_version}
		configuredDevice={String(data.settings.values.OCR_DEVICE ?? 'auto')}
		configuredWorkers={Number(data.settings.values.OCR_WORKERS ?? 0)}
	/>
{/if}

<SectionDivider label="Selection Rules" note="What the pipeline accepts, and what that removes" />

<!-- One card, two halves: the rule, then what it rejected. Split across separate
     cards they scrolled apart and stopped explaining each other. -->
<section class="rules">
	<TextProfilePanel initial={data.textProfiles} flat />
	<CandidateFunnel
		movie={data.metrics}
		tv={data.tvMetrics}
		activeProfile={activeMovieProfile}
		flat
	/>
</section>

<SectionDivider label="Operations" />

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
				<h2>Poster Maintenance</h2>
				<p>Backups, heal scans, and cache cleanup.</p>
			</div>
		</div>
		<a class="pill quiet" href="/settings?tab=posters"
			>Poster settings <Icon name="chevron" size={13} /></a
		>
	</header>

	<div class="maintenance-body">
		<div class="maintenance-facts">
			<div>
				<span>Backups</span><b>{summary.backups.count}</b><small
					>{bytesH(summary.backups.bytes)}</small
				>
			</div>
			<div>
				<span>Last Heal</span><b>{isoDate(summary.last_heal?.last_run)}</b><small
					>most recent scan</small
				>
			</div>
			<div>
				<span>Next Heal</span><b>{isoDate(summary.heal_schedule?.next_run_at)}</b><small
					>configured schedule</small
				>
			</div>
			<!-- Sits beside "Clean orphaned cache" so the size is visible before the
			     destructive click, not discovered by making it. -->
			<div>
				<span>Cache</span><b>{bytesH(data.cache?.total_bytes ?? 0)}</b><small>
					{data.cache ? `${bytesH(data.cache.clearable_bytes)} clearable` : 'size unavailable'}
				</small>
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
			<b>Destructive Actions</b>
			<span>Permanent. Each one asks for confirmation first.</span>
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
	message="This removes deployed film posters and marks those films as missing so the pipeline can run again. Cached candidates and taste artifacts are preserved."
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
		flex-wrap: wrap;
		gap: 8px 12px;
		margin: 2px 0 7px;
	}
	.eyebrow {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		font-weight: 700;
		color: var(--faint);
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
	.workspace-link {
		margin-left: auto;
	}
	/* A blocker, not a metric: these shows cannot run at all until they are matched,
	   so it earns a place in the head instead of recolouring a card about something
	   else. Small coloured text is pulled toward the foreground colour so it clears
	   the contrast floor in both themes. */
	.row-warn {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 2px 9px;
		border: 1px solid color-mix(in srgb, var(--warn) 32%, transparent);
		border-radius: 999px;
		background: color-mix(in srgb, var(--warn) 10%, transparent);
		color: color-mix(in srgb, var(--warn) 58%, var(--text));
		font-size: 11px;
		font-weight: 600;
	}
	.row-warn:hover {
		background: color-mix(in srgb, var(--warn) 18%, transparent);
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(var(--cols, 5), minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 14px;
	}
	/* The shared border that makes the profile rule and its funnel one object. */
	.rules {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		margin-bottom: 18px;
		overflow: hidden;
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
	.maintenance-body {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 18px;
		padding: 15px 16px;
	}
	.maintenance-facts {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
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
		.stats {
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
