<script lang="ts">
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import PosterLibraryToggle from '$lib/components/PosterLibraryToggle.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		enrichProfile,
		getTasteStatus,
		rebuildTasteMap,
		retrainResidual,
		retrainTaste
	} from '$lib/api/taste';
	import { countOfSubjects } from '$lib/taste/library-copy';
	import { toast } from '$lib/toast';
	import type { TasteStatus } from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The library switch is a link, so the URL is the single source of truth and every
	// panel below is seeded from a fresh `load`.
	const library = $derived<'movies' | 'tv'>(data.library ?? 'movies');
	const mapHref = $derived(library === 'tv' ? '/taste/map?library=tv' : '/taste/map');
	const historyHref = $derived(library === 'tv' ? '/taste/history?library=tv' : '/taste/history');

	// What `load` returned, until finished work gives us something newer. Reading the
	// loaded value directly is what lets the server render the real numbers instead of
	// a flash of the "unavailable" state before hydration.
	let refreshed = $state<TasteStatus | null>(null);
	const status = $derived(refreshed ?? data.status);

	let rebuilding = $state(false);
	let residualTraining = $state(false);
	let enriching = $state(false);
	let mapRebuilding = $state(false);
	let rebuildJobId = $state<string | null>(null);
	let residualJobId = $state<string | null>(null);
	let initiatedJobIds = $state<string[]>([]);

	// Navigating between libraries re-runs `load`. The in-flight flags belong to the
	// library that started the work, so they reset here too — a film rebuild must not
	// leave the television button reading "Rebuilding…".
	$effect(() => {
		void data;
		refreshed = null;
		rebuilding = false;
		residualTraining = false;
		enriching = false;
		mapRebuilding = false;
		rebuildJobId = null;
		residualJobId = null;
		initiatedJobIds = [];
	});

	const residual = $derived(status?.ranking_residual);
	const exemplars = $derived(status?.exemplars);
	const labels = $derived(status?.labels);
	const ready = $derived(
		!!residual &&
			residual.activation.subjects.have >= residual.activation.subjects.need &&
			residual.activation.pairs.have >= residual.activation.pairs.need
	);
	const subjectCount = $derived(exemplars?.unique_movies ?? 0);

	// One activity feed, scoped to this library's three taste artifacts, so the film
	// tab never narrates television work and vice versa.
	const subjectReferences = $derived([
		`taste_profile:${library}`,
		`ranking_residual:${library}`,
		`taste_map:${library}`
	]);

	function pct(have: number, need: number): number {
		return need > 0 ? Math.min(100, (have / need) * 100) : 100;
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return 'never';
		const t = new Date(iso);
		return Number.isNaN(t.getTime()) ? 'never' : t.toLocaleDateString();
	}

	function track(jobId: string) {
		initiatedJobIds = [...new Set([...initiatedJobIds, jobId])];
	}

	async function startRebuild(source: 'canonical' | 'seeding_bundle' = 'canonical') {
		if (rebuilding) return;
		rebuilding = true;
		try {
			const job = await retrainTaste(fetch, library, source);
			rebuildJobId = job.job_id;
			track(job.job_id);
			toast('Taste rebuild queued', 'info');
		} catch (e) {
			rebuilding = false;
			toast(e instanceof Error ? e.message : 'Rebuild failed to start', 'bad');
		}
	}

	async function startResidual() {
		if (residualTraining) return;
		residualTraining = true;
		try {
			const job = await retrainResidual(fetch, library);
			residualJobId = job.job_id;
			track(job.job_id);
			toast('Bounded residual training queued', 'info');
		} catch (e) {
			residualTraining = false;
			toast(e instanceof Error ? e.message : 'Training failed to start', 'bad');
		}
	}

	async function startMapRebuild() {
		if (mapRebuilding) return;
		mapRebuilding = true;
		try {
			const job = await rebuildTasteMap(fetch, library);
			track(job.job_id);
			toast(job.idempotent ? 'Taste map rebuild already running' : 'Taste map rebuild queued');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Map rebuild failed to start', 'bad');
		} finally {
			mapRebuilding = false;
		}
	}

	async function startEnrich() {
		if (enriching) return;
		enriching = true;
		try {
			const job = await enrichProfile(fetch, library);
			track(job.job_id);
			toast(job.idempotent ? 'Enrichment already running' : 'Metadata enrichment queued');
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Enrichment failed to start', 'bad');
		} finally {
			enriching = false;
		}
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		if (snapshot.job_id === rebuildJobId) rebuilding = false;
		if (snapshot.job_id === residualJobId) residualTraining = false;
		toast(
			`Taste work ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		if (snapshot.status.outcome === 'succeeded' || snapshot.status.outcome === 'no_change') {
			try {
				refreshed = await getTasteStatus(fetch, library);
			} catch {
				/* keep the stale numbers rather than blanking the page */
			}
		}
	}
</script>

<header class="scope">
	<PosterLibraryToggle
		active={library === 'tv' ? 'television' : 'films'}
		films="/taste?library=movies"
		television="/taste?library=tv"
		label="Taste library"
	/>
	<a class="pill quiet" href="/settings?tab=taste">
		<Icon name="settings" size={14} /> Taste Settings
	</a>
</header>

{#if data.error || !status}
	<div class="empty">
		<Icon name="taste" size={34} stroke={1} />
		<strong>Taste status unavailable</strong>
		<span>{data.error ?? 'Could not reach the taste service.'}</span>
	</div>
{:else}
	<h1 class="sr-only">Key Art Engine</h1>

	<section class="engine" class:on={residual?.active}>
		<div class="engine-head">
			<span class="eyebrow">Key Art Engine</span>
			<span class="state">
				<StatusDot tone={residual?.active ? 'good' : 'muted'} size={7} />
				{residual?.active ? 'Active' : 'Inactive'}
			</span>
		</div>

		<div class="engine-body">
			<div class="reading">
				<div class="figure mono">{residual?.pairs ?? 0}</div>
				<div class="figure-label">preference pairs</div>
				{#if labels}
					<p class="labels">
						{labels.total} labels · {labels.positives} positive · {labels.negatives} negative
					</p>
				{/if}
			</div>

			{#if residual}
				<div class="gauges">
					{#each [{ name: 'Subjects', gauge: residual.activation.subjects }, { name: 'Pairs', gauge: residual.activation.pairs }] as row (row.name)}
						<div class="gauge">
							<div class="gauge-head">
								<span>{row.name}</span>
								<span class="mono">{row.gauge.have} / {row.gauge.need}</span>
							</div>
							<ProgressBar
								value={pct(row.gauge.have, row.gauge.need)}
								tone={row.gauge.have >= row.gauge.need ? 'good' : 'gold'}
								height={5}
							/>
						</div>
					{/each}
				</div>
			{/if}

			<div class="engine-action">
				<button class="pill primary" onclick={startResidual} disabled={residualTraining || !ready}>
					{residualTraining ? 'Training…' : 'Train bounded residual'}
				</button>
			</div>
		</div>
	</section>

	{#if status.gate_alerts?.length}
		<div class="alerts">
			{#each status.gate_alerts as alert (alert.gate)}
				<div class="alert">
					<StatusDot tone="warn" size={6} />
					<span class="gate">{alert.gate}</span>
					<span>overridden {alert.overrides}× at the current threshold</span>
				</div>
			{/each}
		</div>
	{/if}

	<div class="cards">
		<section class="card">
			<div class="card-head">
				<h2>Taste Profile</h2>
				<a class="link" href={historyHref}>History</a>
			</div>
			<div class="figure mono">{exemplars?.count ?? 0}</div>
			<div class="figure-label">
				exemplars · {countOfSubjects(library, subjectCount)}
				{#if exemplars?.negatives}· {exemplars.negatives} negative{/if}
			</div>
			<dl class="facts">
				<div>
					<dt>Built</dt>
					<dd>{fmtDate(exemplars?.last_rebuild)}</dd>
				</div>
				{#if exemplars?.duplicate_groups}
					<div>
						<dt>Duplicates</dt>
						<dd>{exemplars.duplicate_groups}</dd>
					</div>
				{/if}
			</dl>
			<div class="card-actions">
				<button class="pill primary" onclick={() => startRebuild()} disabled={rebuilding}>
					{rebuilding ? 'Rebuilding…' : 'Rebuild profile'}
				</button>
				<!-- TEMPORARY (seeding bundle): remove with the API `source` enum value. -->
				{#if library === 'movies'}
					<button
						class="pill ghost"
						onclick={() => startRebuild('seeding_bundle')}
						disabled={rebuilding}
						title="Trains on the curated posters on disk instead of recorded evidence. The
new profile carries no negative exemplars, and the current bounded residual
stops applying until it is retrained against it."
					>
						Build from seeding bundle
					</button>
				{/if}
			</div>
		</section>

		<section class="card">
			<div class="card-head">
				<h2>Taste Map</h2>
				<a class="link" href={mapHref}>Open</a>
			</div>
			<a class="preview" href={mapHref} aria-label="Open the taste map">
				<span class="constellation" aria-hidden="true">
					{#each Array.from({ length: 34 }, (_, i) => i) as dot (dot)}
						<i style="--x:{(dot * 37) % 97}%; --y:{(dot * 61) % 89}%; --d:{(dot % 7) * 0.12}s"></i>
					{/each}
				</span>
			</a>
			<div class="card-actions">
				<button class="pill quiet" onclick={startMapRebuild} disabled={mapRebuilding}>
					{mapRebuilding ? 'Queuing…' : 'Rebuild map'}
				</button>
				<button class="pill ghost" onclick={startEnrich} disabled={enriching}>
					{enriching ? 'Queuing…' : 'Enrich metadata'}
				</button>
			</div>
		</section>
	</div>

	<FeatureActivityPanel
		scopeKey={`feature:taste:${library}`}
		query={{ feature_area: 'ml_taste', subject_reference: subjectReferences }}
		jobIds={initiatedJobIds}
		heading="Taste Training Activity"
		onSettled={handleJobSettled}
	/>
{/if}

<style>
	.scope {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		flex-wrap: wrap;
		margin-bottom: 18px;
	}
	.scope :global(.library-switch) {
		margin-bottom: 0;
	}
	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 8px;
		padding: 70px 24px;
		text-align: center;
		color: var(--faint);
		border: 1px dashed var(--line2);
		border-radius: var(--radius);
		background: var(--panel);
	}

	.engine,
	.card,
	.alerts {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}

	/* The one binary this page exists to answer, so it reads as lit when it is. */
	.engine {
		padding: 20px 22px;
		margin-bottom: 14px;
	}
	.engine.on {
		border-color: color-mix(in srgb, var(--good) 45%, var(--line));
		box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--good) 12%, transparent);
	}
	.engine-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 14px;
	}
	.eyebrow {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.state {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		color: var(--muted);
	}
	/* Reading, gauges, action: what it is, how far off it is, what you can do. */
	.engine-body {
		display: grid;
		grid-template-columns: minmax(200px, 260px) minmax(0, 1fr) auto;
		align-items: center;
		gap: 28px;
	}
	.figure {
		font-size: 34px;
		font-weight: 600;
		line-height: 1;
		letter-spacing: -0.02em;
	}
	.figure-label {
		margin-top: 5px;
		font-size: 12px;
		color: var(--muted);
	}
	.labels {
		margin: 10px 0 0;
		font-size: 11.5px;
		color: var(--faint);
	}
	.gauges {
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-width: 0;
	}
	.gauge-head {
		display: flex;
		justify-content: space-between;
		font-size: 11.5px;
		color: var(--muted);
		margin-bottom: 5px;
	}

	.alerts {
		padding: 12px 16px;
		margin-bottom: 14px;
		border-color: color-mix(in srgb, var(--warn) 35%, var(--line));
	}
	.alert {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12.5px;
		color: var(--muted);
	}
	.alert .gate {
		color: var(--warn);
		font-weight: 600;
	}

	.cards {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
		gap: 14px;
		margin-bottom: 18px;
	}
	.card {
		display: flex;
		flex-direction: column;
		padding: 18px 20px;
	}
	.card-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 14px;
	}
	.card h2 {
		margin: 0;
		font-size: 14px;
		font-weight: 650;
	}
	.link {
		font-size: 12px;
		color: var(--muted);
	}
	.link:hover {
		color: var(--gold-copy);
	}
	.facts {
		display: flex;
		flex-wrap: wrap;
		gap: 18px;
		margin: 16px 0 0;
	}
	.facts div {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.facts dt {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
	}
	.facts dd {
		margin: 0;
		font-size: 13px;
		color: var(--text);
	}
	.card-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-top: auto;
		padding-top: 18px;
	}

	/* A scatter of the thing the link opens, rather than a screenshot that would go
	   stale or a caption explaining what a map is. */
	.preview {
		display: block;
		position: relative;
		flex: 1;
		min-height: 96px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--ink);
		overflow: hidden;
	}
	.preview:hover {
		border-color: color-mix(in srgb, var(--gold) 40%, var(--line));
	}
	.preview:focus-visible {
		outline: 2px solid var(--gold);
		outline-offset: 2px;
	}
	.constellation i {
		position: absolute;
		left: var(--x);
		top: var(--y);
		width: 4px;
		height: 4px;
		border-radius: 50%;
		background: var(--gold);
		opacity: 0.5;
		animation: mq-pulse 3.6s var(--d) infinite;
	}
	.constellation i:nth-child(3n) {
		background: var(--info);
	}
	.constellation i:nth-child(5n) {
		background: var(--good);
	}
	@media (prefers-reduced-motion: reduce) {
		.constellation i {
			animation: none;
		}
	}

	@media (max-width: 900px) {
		.engine-body {
			grid-template-columns: minmax(0, 1fr);
			gap: 18px;
		}
		.engine-action {
			justify-self: start;
		}
	}
</style>
