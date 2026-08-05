<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import PosterLibraryToggle from '$lib/components/PosterLibraryToggle.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import {
		enrichProfile,
		getRankingResidualDetail,
		getRankingResiduals,
		getTasteMap,
		rebuildTasteMap,
		getTasteProfileDetail,
		getTasteProfiles,
		getTasteStatus,
		retrainResidual,
		retrainTaste
	} from '$lib/api/taste';
	import { assetBreakdown, countOfSubjects, subjectNounTitle } from '$lib/taste/library-copy';
	import { toast } from '$lib/toast';
	import type {
		ManagedArtifactSummary,
		ManagedResidualDetail,
		ManagedProfileDetail,
		TasteMapData,
		TasteStatus
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The library switch is a link now, so the URL is the single source of truth and
	// every panel below is seeded from a fresh `load`.
	const library = $derived<'movies' | 'tv'>(data.library ?? 'movies');

	let status = $state<TasteStatus | null>(null);
	let profiles = $state<ManagedArtifactSummary[]>([]);
	let residuals = $state<ManagedArtifactSummary[]>([]);
	let mapData = $state<TasteMapData | null>(null);
	let mapLoading = $state(false);
	let mapError = $state<string | null>(null);
	let detailLoading = $state(false);

	let selectedProfileId = $state<string | null>(null);
	let selectedResidualId = $state<string | null>(null);
	let profileDetail = $state<ManagedProfileDetail | null>(null);
	let residualDetail = $state<ManagedResidualDetail | null>(null);
	// SvelteSet is reactive on its own; it is cleared rather than replaced.
	const expandedSubjects = new SvelteSet<string>();

	// Navigating between libraries re-runs `load`; without this every panel would keep
	// rendering the previous library's numbers under the new tab.
	$effect(() => {
		const next = data;
		status = next.status;
		profiles = next.profiles ?? [];
		residuals = next.residuals ?? [];
		mapData = null;
		mapError = null;
		mapRequestedFor = null;
		selectedProfileId = null;
		selectedResidualId = null;
		profileDetail = null;
		residualDetail = null;
		expandedSubjects.clear();
	});

	function preferredArtifactId(
		rows: ManagedArtifactSummary[],
		currentId: string | null
	): string | null {
		if (currentId && rows.some((row) => row.id === currentId)) return currentId;
		return rows.find((row) => row.status === 'active')?.id ?? rows[0]?.id ?? null;
	}

	// ── Taste map ────────────────────────────────────────────────────────────────
	// Open by default, but fetched client-side rather than in `load`: the projection is
	// a large per-exemplar payload that SSR would serialise twice, and /taste/map 404s
	// whenever a namespace has no published map — which must not take the page down.
	let mapVisible = $state(true);
	let mapRequestedFor = $state<string | null>(null);

	$effect(() => {
		if (!mapVisible) return;
		if (mapRequestedFor === library) return;
		mapRequestedFor = library;
		void loadMap();
	});

	async function loadMap() {
		if (mapLoading) return;
		mapLoading = true;
		mapError = null;
		try {
			mapData = await getTasteMap(fetch, library);
		} catch (e) {
			mapError = e instanceof Error ? e.message : 'Could not load taste map';
		} finally {
			mapLoading = false;
		}
	}

	function toggleMap() {
		mapVisible = !mapVisible;
	}

	async function refresh() {
		try {
			const [nextStatus, nextProfiles, nextResiduals] = await Promise.all([
				getTasteStatus(fetch, library),
				getTasteProfiles(fetch, library).then((value) => value.profiles),
				getRankingResiduals(fetch, library).then((value) => value.residuals)
			]);
			status = nextStatus;
			profiles = nextProfiles;
			residuals = nextResiduals;
			const nextProfileId = preferredArtifactId(nextProfiles, selectedProfileId);
			const nextResidualId = preferredArtifactId(nextResiduals, selectedResidualId);
			if (nextProfileId) {
				await loadProfileDetail(nextProfileId);
			} else {
				selectedProfileId = null;
				profileDetail = null;
			}
			if (nextResidualId) {
				await loadResidualDetail(nextResidualId);
			} else {
				selectedResidualId = null;
				residualDetail = null;
			}
			if (mapVisible) void loadMap();
		} catch {
			/* keep stale */
		}
	}

	let rebuilding = $state(false);
	let rebuildJobId = $state<string | null>(null);
	let residualJobId = $state<string | null>(null);
	let initiatedJobIds = $state<string[]>([]);

	async function startRebuild(source: 'canonical' | 'seeding_bundle' = 'canonical') {
		if (rebuilding) return;
		rebuilding = true;
		try {
			const job = await retrainTaste(fetch, library, source);
			rebuildJobId = job.job_id;
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			toast('Taste rebuild queued', 'info');
		} catch (e) {
			rebuilding = false;
			toast(e instanceof Error ? e.message : 'Rebuild failed to start', 'bad');
		}
	}

	let residualTraining = $state(false);
	const residual = $derived(status?.ranking_residual);
	const ready = $derived(
		!!residual &&
			residual.activation.subjects.have >= residual.activation.subjects.need &&
			residual.activation.pairs.have >= residual.activation.pairs.need
	);

	async function startResidual() {
		if (residualTraining) return;
		residualTraining = true;
		try {
			const job = await retrainResidual(fetch, library);
			residualJobId = job.job_id;
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			toast('Bounded residual training queued', 'info');
		} catch (e) {
			residualTraining = false;
			toast(e instanceof Error ? e.message : 'Training failed to start', 'bad');
		}
	}

	async function rebuildMap() {
		if (mapLoading) return;
		mapLoading = true;
		mapError = null;
		try {
			const job = await rebuildTasteMap(fetch, library);
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			toast(
				job.idempotent ? 'Taste map rebuild already active' : 'Taste map rebuild queued',
				'info'
			);
		} catch (e) {
			mapError = e instanceof Error ? e.message : 'Map rebuild failed';
			toast(mapError, 'bad');
		} finally {
			mapLoading = false;
		}
	}

	let enriching = $state(false);

	async function runEnrich() {
		if (enriching) return;
		enriching = true;
		try {
			const job = await enrichProfile(fetch, library);
			initiatedJobIds = [...new Set([...initiatedJobIds, job.job_id])];
			toast(
				job.idempotent ? 'Profile enrichment already active' : 'Profile enrichment queued',
				'info'
			);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Enrichment failed', 'bad');
		} finally {
			enriching = false;
		}
	}

	async function loadProfileDetail(artifactId: string) {
		selectedProfileId = artifactId;
		detailLoading = true;
		try {
			// The detail payload carries every poster per subject, so the flat
			// /exemplars endpoint is no longer a second round trip the page needs.
			profileDetail = await getTasteProfileDetail(fetch, artifactId, library);
			expandedSubjects.clear();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not load profile details', 'bad');
		} finally {
			detailLoading = false;
		}
	}

	async function loadResidualDetail(artifactId: string) {
		selectedResidualId = artifactId;
		detailLoading = true;
		try {
			residualDetail = await getRankingResidualDetail(fetch, artifactId, library);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not load residual details', 'bad');
		} finally {
			detailLoading = false;
		}
	}

	function toggleSubject(key: string) {
		if (expandedSubjects.has(key)) expandedSubjects.delete(key);
		else expandedSubjects.add(key);
	}

	function pct(have: number, need: number): number {
		return need > 0 ? Math.min(100, (have / need) * 100) : 100;
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return 'never';
		const t = new Date(iso);
		return Number.isNaN(t.getTime()) ? 'never' : t.toLocaleString();
	}

	function titleOf(title: string, year: number | null | undefined): string {
		return year ? `${title} (${year})` : title;
	}

	async function handleJobSettled(snapshot: JobSnapshotResponse) {
		if (snapshot.job_id === rebuildJobId) rebuilding = false;
		if (snapshot.job_id === residualJobId) residualTraining = false;
		toast(
			`Taste work ${snapshot.status.label.toLowerCase()}`,
			snapshot.status.outcome === 'succeeded' ? 'good' : 'bad'
		);
		if (snapshot.status.outcome === 'succeeded' || snapshot.status.outcome === 'no_change') {
			await refresh();
		}
	}

	$effect(() => {
		if (!selectedProfileId && profiles.length) {
			void loadProfileDetail(preferredArtifactId(profiles, null) ?? profiles[0].id);
		}
	});

	$effect(() => {
		if (!selectedResidualId && residuals.length) {
			void loadResidualDetail(preferredArtifactId(residuals, null) ?? residuals[0].id);
		}
	});

	const genreEntries = $derived(Object.entries(status?.labels.genres ?? {}).slice(0, 12));
	const subjectCount = $derived(status?.labels.subjects ?? status?.labels.movies ?? 0);
</script>

<header class="workspace-head">
	<div class="head-top">
		<div class="titles">
			<!-- The visible title lives in the app top bar; this keeps the page from being
			     headless and the heading order from jumping straight to h3. -->
			<h1 class="sr-only">Key Art Engine</h1>
			<p>Taste profile &amp; bounded preference residual</p>
		</div>
		<div class="scope">
			<a class="pill quiet" href="/settings?tab=taste">
				<Icon name="settings" size={14} /> Taste settings
			</a>
			<PosterLibraryToggle
				active={library === 'tv' ? 'television' : 'films'}
				films="/taste?library=movies"
				television="/taste?library=tv"
				label="Taste library"
			/>
		</div>
	</div>
</header>

{#if data.error || !status}
	<div class="empty">
		<Icon name="taste" size={34} stroke={1} />
		<strong>Taste status unavailable</strong>
		<span>{data.error ?? 'Could not reach the taste service.'}</span>
	</div>
{:else}
	<div class="stat-grid">
		<StatCard
			label="Labels"
			value={status.labels.total}
			sub={`${status.labels.positives} positive · ${status.labels.negatives} negative · ${countOfSubjects(library, subjectCount)}`}
			note="Preference events you have recorded. Approvals, selections, overrides and rank changes count positive; hates count negative."
			tone="gold"
		/>
		<StatCard
			label="Exemplars"
			value={status.exemplars.count}
			sub={`${countOfSubjects(library, status.exemplars.unique_movies ?? 0)} · ${status.exemplars.duplicate_groups ?? 0} duplicate groups`}
			note={`Poster images baked into the active profile's embedding matrix. ${countOfSubjects(library, status.exemplars.unique_movies ?? 0)} contributed them.`}
			tone="info"
		/>
		<div class="card engine" class:on={residual?.active}>
			<div class="engine-head">
				<span class="label">Key Art Engine</span>
				<span class="engine-state" style="--c:{residual?.active ? 'var(--good)' : 'var(--faint)'}">
					<StatusDot tone={residual?.active ? 'good' : 'muted'} size={7} />
					{residual?.active ? 'Active' : 'Inactive'}
				</span>
			</div>
			<div class="engine-val mono">{residual?.pairs ?? 0}<span class="unit"> pairs</span></div>
			{#if residual}
				<div class="gauges">
					<div class="gauge">
						<div class="gl">
							<span>Subjects</span><span class="mono"
								>{residual.activation.subjects.have}/{residual.activation.subjects.need}</span
							>
						</div>
						<ProgressBar
							value={pct(residual.activation.subjects.have, residual.activation.subjects.need)}
							tone={residual.activation.subjects.have >= residual.activation.subjects.need
								? 'good'
								: 'gold'}
							height={5}
						/>
					</div>
					<div class="gauge">
						<div class="gl">
							<span>Pairs</span><span class="mono"
								>{residual.activation.pairs.have}/{residual.activation.pairs.need}</span
							>
						</div>
						<ProgressBar
							value={pct(residual.activation.pairs.have, residual.activation.pairs.need)}
							tone={residual.activation.pairs.have >= residual.activation.pairs.need
								? 'good'
								: 'gold'}
							height={5}
						/>
					</div>
				</div>
				<p class="engine-note">
					<b>Subjects</b> are distinct titles that produced at least one training pair.
					<b>Pairs</b> are the pairwise comparisons derived from them. The engine activates at
					{residual.activation.subjects.need} subjects and {residual.activation.pairs.need} pairs.
				</p>
			{/if}
		</div>
	</div>

	{#if status.gate_alerts?.length}
		<div class="alert-panel">
			<div class="alert-head">Gate override alerts</div>
			{#each status.gate_alerts as a (a.gate)}
				<div class="alert-row">
					<StatusDot tone="warn" size={6} />
					<span class="ar-gate">{a.gate}</span>
					<span class="ar-note">overridden {a.overrides}× at the current threshold</span>
				</div>
			{/each}
		</div>
	{/if}

	<FeatureActivityPanel
		scopeKey={`feature:taste:${library}`}
		query={{ feature_area: 'ml_taste' }}
		jobIds={initiatedJobIds}
		heading="Taste training activity"
		onSettled={handleJobSettled}
	/>

	<div class="train-cols">
		<section class="train-card">
			<h3>Rebuild taste profile</h3>
			<p class="card-note">
				{library === 'tv'
					? 'Scans the show and season artwork already deployed in the library and builds a new immutable profile from it.'
					: 'Builds a new immutable profile from canonical approved poster evidence.'}
			</p>
			<div class="card-actions">
				<button class="pill primary" onclick={() => startRebuild()} disabled={rebuilding}>
					{rebuilding ? 'Rebuilding…' : 'Rebuild profile'}
				</button>
			</div>
			<!-- TEMPORARY (seeding bundle): remove with the API `source` enum value. -->
			{#if library === 'movies'}
				<div class="temp-seed">
					<button
						class="pill quiet"
						onclick={() => startRebuild('seeding_bundle')}
						disabled={rebuilding}
					>
						Build from seeding bundle
					</button>
					<p class="temp-note">
						Temporary. Trains on the curated posters on disk instead of recorded evidence, so the
						profile carries no negative exemplars, and the current bounded residual stops applying
						until it is retrained against the new generation.
					</p>
				</div>
			{/if}
		</section>

		<section class="train-card">
			<h3>Train the bounded residual</h3>
			<p class="card-note">
				Canonical approvals and overrides accumulate preference pairs. Training adds a bounded
				correction to the weighted baseline only when held-out ranking improves.
			</p>
			{#if !ready}
				<div class="hint">
					Needs more data to activate. Keep approving posters to reach the thresholds above.
				</div>
			{/if}
			<div class="card-actions">
				<button class="pill primary" onclick={startResidual} disabled={residualTraining || !ready}>
					{residualTraining ? 'Training…' : 'Train bounded residual'}
				</button>
			</div>
		</section>
	</div>

	<div class="manager-grid">
		<section class="manager-card">
			<div class="panel-head">
				<h3>Taste profiles</h3>
				<p>Immutable native profiles and their canonical producer generations.</p>
			</div>
			<div class="manager-body">
				<div class="rail" role="listbox" aria-label="Taste profile generations" tabindex="-1">
					{#each profiles as profile (profile.id)}
						<button
							class="rail-row"
							class:selected={selectedProfileId === profile.id}
							role="option"
							aria-selected={selectedProfileId === profile.id}
							onclick={() => loadProfileDetail(profile.id)}
						>
							<span class="rail-title">
								<strong>{profile.label}</strong>
								<span class="badge" class:active={profile.status === 'active'}>
									{profile.status}
								</span>
							</span>
							<span class="rail-meta">{profile.summary.exemplars ?? 0} exemplars</span>
							<span class="rail-meta">{fmtDate(profile.updated_at)}</span>
						</button>
					{/each}
					{#if profiles.length === 0}
						<div class="detail-empty">No canonical taste profiles yet.</div>
					{/if}
				</div>

				<div class="detail-pane">
					{#if detailLoading && selectedProfileId && !profileDetail}
						<div class="detail-empty">Loading profile details…</div>
					{:else if profileDetail}
						<div class="detail-head">
							<div>
								<h4>{profileDetail.label}</h4>
								<div class="artifact-date">
									Created {fmtDate(profileDetail.created_at)} · Activated {fmtDate(
										profileDetail.activated_at
									)} · {profileDetail.source_mode ?? 'unknown source'}
								</div>
							</div>
							<span class="badge" class:active={profileDetail.status === 'active'}
								>{profileDetail.status}</span
							>
						</div>
						<div class="metric-strip">
							<div>
								<span>Exemplars</span><b class="mono">{profileDetail.summary.exemplars ?? 0}</b>
							</div>
							<div>
								<span>{subjectNounTitle(library)}</span>
								<b class="mono"
									>{profileDetail.summary.unique_subjects ??
										profileDetail.summary.unique_movies ??
										0}</b
								>
							</div>
							<div>
								<span>Negatives</span>
								<b class="mono">{profileDetail.summary.negative_exemplars ?? 0}</b>
							</div>
							<div>
								<span>Duplicates</span>
								<b class="mono">{profileDetail.summary.duplicate_groups ?? 0}</b>
							</div>
						</div>

						<h5>
							Contributing {subjectNounTitle(library).toLowerCase()}
							<span class="h5-note">Expand a row to see the posters it contributed.</span>
						</h5>
						<div class="detail-list">
							{#each profileDetail.movies as subject (subject.subject_key)}
								<div class="subject">
									<button
										class="subject-row"
										aria-expanded={expandedSubjects.has(subject.subject_key)}
										onclick={() => toggleSubject(subject.subject_key)}
									>
										<span class="chev" aria-hidden="true"
											>{expandedSubjects.has(subject.subject_key) ? '▾' : '▸'}</span
										>
										<span class="s-title">{titleOf(subject.title, subject.year)}</span>
										<span class="s-assets"
											>{subject.asset_summary || assetBreakdown(subject.asset_counts ?? {})}</span
										>
										<span class="mono s-count">{subject.contribution_count}</span>
									</button>
									{#if expandedSubjects.has(subject.subject_key)}
										<div class="asset-list">
											{#each subject.assets ?? [] as asset (asset.name)}
												<div class="asset-row">
													<span>{asset.label}</span>
													{#if asset.is_duplicate}
														<span class="dup">dup ×{asset.duplicate_count}</span>
													{/if}
												</div>
											{/each}
										</div>
									{/if}
								</div>
							{/each}
							{#if profileDetail.movies.length === 0}
								<div class="detail-empty">This profile has no exemplars.</div>
							{/if}
						</div>

						{#if profileDetail.duplicate_groups.length}
							<h5>
								Duplicate groups
								<span class="h5-note">The same poster staged more than once.</span>
							</h5>
							<div class="detail-list short">
								{#each profileDetail.duplicate_groups as group (`${group.label}-${group.season_number}`)}
									<div class="detail-row">
										<span>{group.label}</span>
										<span class="mono">×{group.count}</span>
									</div>
								{/each}
							</div>
						{/if}
					{:else if profiles.length}
						<div class="detail-empty">Select a generation to inspect it.</div>
					{/if}
				</div>
			</div>
		</section>

		<section class="manager-card">
			<div class="panel-head">
				<h3>Ranking residuals</h3>
				<p>Immutable bounded residuals, producer generations, and held-out gains.</p>
			</div>
			<div class="manager-body">
				<div class="rail" role="listbox" aria-label="Ranking residual generations" tabindex="-1">
					{#each residuals as managedResidual (managedResidual.id)}
						<button
							class="rail-row"
							class:selected={selectedResidualId === managedResidual.id}
							role="option"
							aria-selected={selectedResidualId === managedResidual.id}
							onclick={() => loadResidualDetail(managedResidual.id)}
						>
							<span class="rail-title">
								<strong>{managedResidual.label}</strong>
								<span class="badge" class:active={managedResidual.status === 'active'}>
									{managedResidual.status}
								</span>
							</span>
							<span class="rail-meta"
								>{managedResidual.summary.evaluation?.pair_count ?? 0} pairs</span
							>
							<span class="rail-meta">{fmtDate(managedResidual.trained_at)}</span>
						</button>
					{/each}
					{#if residuals.length === 0}
						<div class="detail-empty">No canonical ranking residuals yet.</div>
					{/if}
				</div>

				<div class="detail-pane">
					{#if detailLoading && selectedResidualId && !residualDetail}
						<div class="detail-empty">Loading residual details…</div>
					{:else if residualDetail}
						<div class="detail-head">
							<div>
								<h4>{residualDetail.label}</h4>
								<div class="artifact-date">
									Created {fmtDate(residualDetail.created_at)} · Trained {fmtDate(
										residualDetail.trained_at
									)}
								</div>
							</div>
							<span class="badge" class:active={residualDetail.status === 'active'}
								>{residualDetail.status}</span
							>
						</div>
						<div class="metric-strip">
							<div>
								<span>Held-out pairs</span>
								<b class="mono">{residualDetail.summary.evaluation?.pair_count ?? 0}</b>
							</div>
							<div>
								<span>Subjects</span>
								<b class="mono">{residualDetail.summary.evaluation?.subject_count ?? 0}</b>
							</div>
							<div>
								<span>Gain</span>
								<b class="mono"
									>{(
										(residualDetail.summary.evaluation?.improvement as number | undefined) ?? 0
									).toFixed(3)}</b
								>
							</div>
							<div>
								<span>Alpha / max Δ</span>
								<b class="mono"
									>{residualDetail.summary.alpha ?? 0} / {residualDetail.summary.delta_max ?? 0}</b
								>
							</div>
						</div>
						<h5>Top residual features</h5>
						<div class="detail-list">
							{#each (residualDetail.summary.top_features ?? []).slice(0, 10) as feature (feature.name)}
								<div class="detail-row">
									<span>{feature.name}</span>
									<span class="mono">{feature.weight.toFixed(3)}</span>
								</div>
							{/each}
						</div>
					{:else if residuals.length}
						<div class="detail-empty">Select a generation to inspect it.</div>
					{/if}
				</div>
			</div>
		</section>
	</div>

	<div class="map-section">
		<div class="head-bar">
			<div class="map-titles">
				<span class="map-title">Taste map</span>
				{#if library === 'tv'}<span class="map-subtitle">Show posters only</span>{/if}
			</div>
			<div class="head-actions">
				<button class="pill quiet" onclick={toggleMap} aria-expanded={mapVisible}>
					{mapVisible ? 'Hide map' : 'Show map'}
				</button>
				<button class="pill ghost" onclick={rebuildMap} disabled={mapLoading}>
					{mapLoading ? 'Building…' : 'Rebuild map'}
				</button>
				<button class="pill ghost" onclick={runEnrich} disabled={enriching || mapLoading}>
					{enriching ? 'Enriching…' : 'Enrich metadata'}
				</button>
			</div>
		</div>
		{#if mapVisible}
			{#if genreEntries.length}
				<div class="genres">
					<span class="chip-label">Genres</span>
					{#each genreEntries as [g, n] (g)}
						<span class="g-chip">{g}<b>{n}</b></span>
					{/each}
				</div>
			{/if}
			<div id="taste-map-panel">
				{#await import('$lib/components/TasteMap.svelte') then module}
					<module.default {mapData} {library} loading={mapLoading} error={mapError} />
				{:catch}
					<p class="detail-empty">Taste map could not be loaded. Rebuild remains available.</p>
				{/await}
			</div>
		{/if}
	</div>
{/if}

<style>
	.scope {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.workspace-head .titles p {
		margin: 0;
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
	.stat-grid,
	.manager-grid,
	.train-cols {
		display: grid;
		gap: 14px;
	}
	.stat-grid {
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		margin-bottom: 16px;
		align-items: start;
	}
	.train-cols,
	.manager-grid {
		grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
	}
	.card,
	.train-card,
	.manager-card,
	.alert-panel {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.card {
		padding: 14px 16px;
	}
	/* The one binary this whole page exists to answer, so it reads as lit when it is. */
	.engine.on {
		border-color: color-mix(in srgb, var(--good) 45%, var(--line));
		box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--good) 12%, transparent);
	}
	.engine-head,
	.detail-head,
	.map-titles {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.map-titles {
		/* Title and scope note read as one label, so they stay together on the left
		   rather than being pushed apart by the toolbar's space-between. */
		justify-content: flex-start;
		gap: 10px;
	}
	.label,
	.chip-label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.engine-state,
	.artifact-date,
	.map-subtitle {
		color: var(--muted);
		font-size: 12px;
	}
	.engine-val {
		font-size: 24px;
		font-weight: 600;
		margin-top: 4px;
	}
	.engine-val .unit {
		font-size: 12px;
		font-family: var(--font-sans);
		color: var(--muted);
	}
	.engine-note {
		margin: 12px 0 0;
		padding-top: 9px;
		border-top: 1px solid var(--line);
		font-size: 11.5px;
		line-height: 1.45;
		color: var(--faint);
	}
	.engine-note b {
		color: var(--muted);
		font-weight: 600;
	}
	.gauges,
	.genres {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.gauges {
		flex-direction: column;
		flex-wrap: nowrap;
		margin-top: 12px;
	}
	.gl {
		display: flex;
		justify-content: space-between;
		font-size: 11px;
		color: var(--muted);
		margin-bottom: 4px;
	}
	.genres {
		align-items: center;
		margin-bottom: 12px;
	}
	.g-chip,
	.badge {
		display: inline-flex;
		align-items: center;
		padding: 4px 9px;
		border-radius: 999px;
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 12px;
	}
	.g-chip {
		gap: 6px;
	}
	.g-chip b {
		color: var(--muted);
	}
	.badge.active {
		background: color-mix(in srgb, var(--good) 12%, var(--panel2));
		border-color: color-mix(in srgb, var(--good) 35%, var(--line));
		color: var(--good);
	}
	.train-card,
	.manager-card {
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.card-note,
	.panel-head p {
		margin: 0;
		color: var(--muted);
		font-size: 12.5px;
		line-height: 1.5;
	}
	.card-actions {
		display: flex;
		gap: 8px;
		margin-top: auto;
	}
	.temp-seed {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 8px;
		padding-top: 12px;
		border-top: 1px dashed var(--line2);
	}
	.temp-note {
		margin: 0;
		font-size: 11.5px;
		line-height: 1.45;
		color: var(--faint);
	}
	.hint {
		font-size: 12px;
		color: var(--warn);
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 22%, transparent);
		border-radius: var(--radius-sm);
		padding: 8px 11px;
	}
	.panel-head h3,
	.train-card h3 {
		margin: 0;
		font-size: 14px;
		font-weight: 650;
	}
	.panel-head {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	/* The generation list is a rail beside the detail, not a stack above it — the
	   detail is what people came for and it used to start below the fold. */
	.manager-body {
		display: grid;
		grid-template-columns: 210px minmax(0, 1fr);
		gap: 14px;
		align-items: start;
	}
	.rail {
		display: flex;
		flex-direction: column;
		gap: 6px;
		max-height: 420px;
		overflow: auto;
	}
	.rail-row {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 4px;
		width: 100%;
		padding: 10px 11px;
		text-align: left;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel2);
		color: var(--text);
	}
	.rail-row:hover {
		border-color: var(--line2);
	}
	.rail-row.selected {
		border-color: color-mix(in srgb, var(--gold) 45%, var(--line));
		background: color-mix(in srgb, var(--gold) 7%, var(--panel2));
	}
	.rail-title {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
	}
	.rail-meta {
		font-size: 11px;
		color: var(--muted);
	}
	.detail-pane {
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-width: 0;
	}
	.detail-head h4 {
		margin: 0;
		font-size: 14px;
		font-weight: 650;
	}
	/* Label over value, so four numbers can be compared at a glance. The 120px track
	   is chosen so four metrics land as 4×1 or 2×2 and never leave a half-empty row
	   showing the divider colour through it. */
	.metric-strip {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
		gap: 1px;
		background: var(--line);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}
	.metric-strip div {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 9px 11px;
		background: var(--panel2);
	}
	.metric-strip span {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
	}
	.metric-strip b {
		font-size: 15px;
		font-weight: 600;
	}
	h5 {
		display: flex;
		align-items: baseline;
		gap: 8px;
		flex-wrap: wrap;
		margin: 4px 0 0;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
	}
	.h5-note {
		text-transform: none;
		letter-spacing: 0;
		font-weight: 400;
		font-size: 11.5px;
	}
	.detail-list {
		display: flex;
		flex-direction: column;
		max-height: 380px;
		overflow: auto;
		padding-right: 4px;
	}
	.detail-list.short {
		max-height: 180px;
	}
	.detail-row {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
		font-size: 13px;
	}
	.detail-row:last-child {
		border-bottom: none;
	}
	.subject {
		border-bottom: 1px solid var(--line);
	}
	.subject:last-child {
		border-bottom: none;
	}
	.subject-row {
		display: grid;
		grid-template-columns: 14px minmax(0, 1fr) auto auto;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 8px 0;
		background: transparent;
		border: none;
		text-align: left;
		color: var(--text);
		font-size: 13px;
	}
	.subject-row:hover .s-title {
		color: var(--gold);
	}
	.chev {
		color: var(--faint);
		font-size: 10px;
	}
	.s-title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.s-assets {
		font-size: 11.5px;
		color: var(--muted);
	}
	.s-count {
		color: var(--muted);
	}
	.asset-list {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 0 0 10px 24px;
	}
	.asset-row {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
		color: var(--muted);
	}
	.dup {
		color: var(--warn);
		font-size: 11px;
	}
	.detail-empty {
		color: var(--faint);
		font-size: 12px;
		padding: 8px 0;
	}
	.alert-panel {
		padding: 14px 16px;
		margin-bottom: 16px;
		border-color: color-mix(in srgb, var(--warn) 35%, var(--line));
	}
	.alert-head {
		font-size: 12px;
		font-weight: 650;
		color: var(--warn);
	}
	.alert-row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding-top: 8px;
		font-size: 12.5px;
	}
	.ar-note {
		color: var(--muted);
	}
	.map-section {
		margin-top: 16px;
	}
	.map-section .head-bar {
		margin-bottom: 14px;
	}
	.map-title {
		font-size: 14px;
		font-weight: 650;
	}
	@media (max-width: 900px) {
		.manager-body {
			grid-template-columns: minmax(0, 1fr);
		}
		.rail {
			flex-direction: row;
			max-height: none;
			overflow-x: auto;
		}
		.rail-row {
			min-width: 190px;
		}
	}
</style>
