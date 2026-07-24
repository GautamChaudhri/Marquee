<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import type { JobSnapshotResponse } from '$lib/activity/types';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
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
		getTasteProfileExemplars,
		getTasteProfiles,
		getTasteStatus,
		retrainResidual,
		retrainTaste
	} from '$lib/api/taste';
	import { toast } from '$lib/toast';
	import type {
		ManagedArtifactSummary,
		ManagedExemplarRow,
		ManagedResidualDetail,
		ManagedProfileDetail,
		TasteMapData,
		TasteSource,
		TasteStatus
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// svelte-ignore state_referenced_locally
	const initialStatus = data.status;
	// svelte-ignore state_referenced_locally
	const initialMapData = data.mapData ?? null;
	// svelte-ignore state_referenced_locally
	const initialProfiles = data.profiles ?? [];
	// svelte-ignore state_referenced_locally
	const initialResiduals = data.residuals ?? [];
	// svelte-ignore state_referenced_locally
	let library = $state<'movies' | 'tv'>(data.library ?? 'movies');

	let status = $state<TasteStatus | null>(initialStatus);
	let mapData = $state<TasteMapData | null>(initialMapData);
	let profiles = $state<ManagedArtifactSummary[]>(initialProfiles);
	let residuals = $state<ManagedArtifactSummary[]>(initialResiduals);
	let mapVisible = $state(false);
	let mapLoading = $state(false);
	let mapError = $state<string | null>(null);
	let detailLoading = $state(false);

	let selectedProfileId = $state<string | null>(null);
	let selectedResidualId = $state<string | null>(null);
	let profileDetail = $state<ManagedProfileDetail | null>(null);
	let residualDetail = $state<ManagedResidualDetail | null>(null);
	let profileExemplars = $state<ManagedExemplarRow[]>([]);

	function preferredArtifactId(
		rows: ManagedArtifactSummary[],
		currentId: string | null
	): string | null {
		if (currentId && rows.some((row) => row.id === currentId)) return currentId;
		return rows.find((row) => row.status === 'active')?.id ?? rows[0]?.id ?? null;
	}

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

	async function toggleMap() {
		mapVisible = !mapVisible;
		if (mapVisible && !mapData) await loadMap();
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
				profileExemplars = [];
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

	const SOURCES = $derived.by<{ id: TasteSource; label: string; hint: string }[]>(() => [
		{
			id: 'training_dir',
			label: 'Training folder',
			hint:
				library === 'movies'
					? 'Movies: data/taste_seeding/movies'
					: 'TV: data/taste_seeding/shows + seasons'
		},
		{ id: 'library', label: 'Library posters', hint: 'Every deployed poster' }
	]);
	let source = $state<TasteSource>('training_dir');
	let rebuilding = $state(false);
	let rebuildJobId = $state<string | null>(null);
	let residualJobId = $state<string | null>(null);
	let initiatedJobIds = $state<string[]>([]);

	async function startRebuild() {
		if (rebuilding) return;
		rebuilding = true;
		try {
			const job = await retrainTaste(fetch, source, library);
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
			const [detail, exemplars] = await Promise.all([
				getTasteProfileDetail(fetch, artifactId, library),
				getTasteProfileExemplars(fetch, artifactId, library).then((value) => value.exemplars)
			]);
			profileDetail = detail;
			profileExemplars = exemplars;
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

	function pct(have: number, need: number): number {
		return need > 0 ? Math.min(100, (have / need) * 100) : 100;
	}

	function setLibrary(next: 'movies' | 'tv') {
		if (next === library) return;
		library = next;
		mapData = null;
		mapError = null;
		const url = new URL(page.url);
		const sp = url.searchParams;
		sp.set('library', next);
		goto(`/taste?${sp.toString()}`);
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return 'never';
		const t = new Date(iso);
		return Number.isNaN(t.getTime()) ? 'never' : t.toLocaleString();
	}

	function metric(row: ManagedArtifactSummary, key: string): number | string {
		return (row.summary?.[key] as number | string | undefined) ?? 0;
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
</script>

<SectionHeader title="Key Art Engine" subtitle="Taste profile & bounded preference residual" />

<div class="library-switch">
	<button class:active={library === 'movies'} onclick={() => setLibrary('movies')}>Movies</button>
	<button class:active={library === 'tv'} onclick={() => setLibrary('tv')}>Television</button>
</div>

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
			sub={`${status.labels.positives} pos · ${status.labels.negatives} neg · ${status.labels.movies} movies`}
			tone="gold"
		/>
		<StatCard
			label="Exemplars"
			value={status.exemplars.count}
			sub={`${status.exemplars.unique_movies ?? 0} movies · ${status.exemplars.duplicate_groups ?? 0} duplicate groups`}
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
			{/if}
		</div>
	</div>

	{#if Object.keys(status.labels.genres).length}
		<div class="genres">
			<span class="chip-label">Genres</span>
			{#each Object.entries(status.labels.genres).slice(0, 12) as [g, n] (g)}
				<span class="g-chip">{g}<b>{n}</b></span>
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
			<h3>Initial training</h3>
			<p class="card-note">
				Rebuild the active taste profile from your curated folder or deployed library posters.
			</p>
			<div class="scope-row">
				{#each SOURCES as s (s.id)}
					<button
						class="scope"
						class:on={source === s.id}
						disabled={rebuilding}
						onclick={() => (source = s.id)}
						title={s.hint}
					>
						{s.label}
						<small>{s.hint}</small>
					</button>
				{/each}
			</div>
			<button class="btn-gold" onclick={startRebuild} disabled={rebuilding}>
				{rebuilding ? 'Rebuilding…' : 'Rebuild profile'}
			</button>
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
			<button class="btn-gold" onclick={startResidual} disabled={residualTraining || !ready}>
				{residualTraining ? 'Training…' : 'Train bounded residual'}
			</button>
		</section>
	</div>

	<div class="manager-grid">
		<section class="manager-card">
			<div class="panel-head">
				<div>
					<h3>Taste profiles</h3>
					<p>Inspect immutable native profiles and their canonical producer generations.</p>
				</div>
			</div>
			<div class="artifact-list">
				{#each profiles as profile (profile.id)}
					<div
						class="artifact-row"
						class:selected={selectedProfileId === profile.id}
						onclick={() => loadProfileDetail(profile.id)}
						onkeydown={(event) => event.key === 'Enter' && loadProfileDetail(profile.id)}
						tabindex="0"
						role="button"
					>
						<div class="artifact-main">
							<div class="artifact-title">
								<strong>{profile.label}</strong>
								<span class="badge" class:active={profile.status === 'active'}>
									{profile.status}
								</span>
							</div>
							<div class="artifact-meta">
								<span>{metric(profile, 'exemplars')} exemplars</span>
								<span>{metric(profile, 'unique_movies')} movies</span>
								<span>{metric(profile, 'duplicate_groups')} duplicate groups</span>
								<span>{profile.source_mode ?? 'unknown source'}</span>
							</div>
							<div class="artifact-date">Updated {fmtDate(profile.updated_at)}</div>
						</div>
					</div>
				{/each}
				{#if profiles.length === 0}
					<div class="detail-empty">No canonical taste profiles yet.</div>
				{/if}
			</div>

			{#if detailLoading && selectedProfileId}
				<div class="detail-empty">Loading profile details…</div>
			{:else if profileDetail}
				<div class="detail-card">
					<div class="detail-head">
						<div>
							<h4>{profileDetail.label}</h4>
							<div class="artifact-date">
								Created {fmtDate(profileDetail.created_at)} · Activated {fmtDate(
									profileDetail.activated_at
								)}
							</div>
						</div>
						<span class="badge" class:active={profileDetail.status === 'active'}
							>{profileDetail.status}</span
						>
					</div>
					<div class="detail-metrics">
						<span>{profileDetail.summary.exemplars ?? 0} exemplars</span>
						<span>{profileDetail.summary.unique_movies ?? 0} movies</span>
						<span>{profileDetail.summary.negative_exemplars ?? 0} negatives</span>
						<span>{profileDetail.summary.duplicate_groups ?? 0} duplicate groups</span>
					</div>
					<div class="detail-columns">
						<div>
							<h5>Contributing movies</h5>
							<div class="detail-list">
								{#each profileDetail.movies as movie (`${movie.movie_id}-${movie.title}`)}
									<div class="detail-row">
										<span>{movie.title}{movie.year ? ` (${movie.year})` : ''}</span>
										<span class="mono">{movie.contribution_count}</span>
									</div>
								{/each}
							</div>
						</div>
						<div>
							<h5>Duplicate groups</h5>
							<div class="detail-list">
								{#if profileDetail.duplicate_groups.length}
									{#each profileDetail.duplicate_groups as group (`${group.title}-${group.year}`)}
										<div class="detail-row">
											<span>{group.title}{group.year ? ` (${group.year})` : ''}</span>
											<span class="mono">{group.count}</span>
										</div>
									{/each}
								{:else}
									<div class="detail-empty">No duplicate groups in this snapshot.</div>
								{/if}
							</div>
						</div>
					</div>
					<div>
						<h5>Immutable exemplars</h5>
						<div class="detail-list exemplars">
							{#each profileExemplars as exemplar (exemplar.name)}
								<div class="detail-row exemplar-row">
									<span
										>{exemplar.title}{exemplar.year
											? ` (${exemplar.year})`
											: ''}{exemplar.is_duplicate
											? ` · dup x${exemplar.duplicate_count}`
											: ''}</span
									>
								</div>
							{/each}
						</div>
					</div>
				</div>
			{/if}
		</section>

		<section class="manager-card">
			<div class="panel-head">
				<div>
					<h3>Ranking residuals</h3>
					<p>View immutable bounded residuals, producer generations, and held-out gains.</p>
				</div>
			</div>
			<div class="artifact-list">
				{#each residuals as managedResidual (managedResidual.id)}
					<div
						class="artifact-row"
						class:selected={selectedResidualId === managedResidual.id}
						onclick={() => loadResidualDetail(managedResidual.id)}
						onkeydown={(event) => event.key === 'Enter' && loadResidualDetail(managedResidual.id)}
						tabindex="0"
						role="button"
					>
						<div class="artifact-main">
							<div class="artifact-title">
								<strong>{managedResidual.label}</strong>
								<span class="badge" class:active={managedResidual.status === 'active'}>
									{managedResidual.status}
								</span>
							</div>
							<div class="artifact-meta">
								<span>{managedResidual.summary.evaluation?.pair_count ?? 0} pairs</span>
								<span>{managedResidual.summary.evaluation?.subject_count ?? 0} subjects</span>
								<span>{managedResidual.summary.mode ?? 'unknown mode'}</span>
							</div>
							<div class="artifact-date">Trained {fmtDate(managedResidual.trained_at)}</div>
						</div>
					</div>
				{/each}
				{#if residuals.length === 0}
					<div class="detail-empty">No canonical ranking residuals yet.</div>
				{/if}
			</div>

			{#if detailLoading && selectedResidualId}
				<div class="detail-empty">Loading residual details…</div>
			{:else if residualDetail}
				<div class="detail-card">
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
					<div class="detail-metrics">
						<span>{residualDetail.summary.evaluation?.pair_count ?? 0} held-out pairs</span>
						<span>{residualDetail.summary.evaluation?.subject_count ?? 0} subjects</span>
						<span
							>{(
								(residualDetail.summary.evaluation?.improvement as number | undefined) ?? 0
							).toFixed(3)} gain</span
						>
					</div>
					<div class="detail-columns">
						<div>
							<h5>Top residual features</h5>
							<div class="detail-list">
								{#each (residualDetail.summary.top_features ?? []).slice(0, 10) as feature (feature.name)}
									<div class="detail-row">
										<span>{feature.name}</span>
										<span class="mono">{feature.weight.toFixed(3)}</span>
									</div>
								{/each}
							</div>
						</div>
						<div>
							<h5>Bounded application</h5>
							<div class="detail-list">
								<div class="detail-row">
									<span>Alpha</span><span class="mono">{residualDetail.summary.alpha ?? 0}</span>
								</div>
								<div class="detail-row">
									<span>Maximum delta</span><span class="mono"
										>{residualDetail.summary.delta_max ?? 0}</span
									>
								</div>
							</div>
						</div>
					</div>
				</div>
			{/if}
		</section>
	</div>

	{#if status.gate_alerts?.length}
		<div class="alert-panel">
			<div class="panel-head">Gate override alerts</div>
			{#each status.gate_alerts as a (a.gate)}
				<div class="alert-row">
					<StatusDot tone="warn" size={6} />
					<span class="ar-gate">{a.gate}</span>
					<span class="ar-note">overridden {a.overrides}× at the current threshold</span>
				</div>
			{/each}
		</div>
	{/if}

	<div class="map-section">
		<div class="map-header">
			<div>
				<span class="map-title">Taste map</span>
				{#if library === 'tv'}<div class="map-subtitle">Show posters only</div>{/if}
				{#if mapData?.summary}
					<div class="map-stats">
						<span>{mapData.summary.exemplars} exemplars</span>
						<span>{mapData.summary.unique_movies} movies</span>
						<span>{mapData.summary.duplicate_groups} duplicate groups</span>
						<span>{mapData.summary.noise} noise</span>
					</div>
				{/if}
			</div>
			<div class="map-actions">
				<button
					class="map-rebuild-btn"
					onclick={toggleMap}
					aria-expanded={mapVisible}
					aria-controls="taste-map-panel"
				>
					{mapVisible ? 'Hide map' : 'Show map'}
				</button>
				<button class="map-rebuild-btn" onclick={rebuildMap} disabled={mapLoading}>
					{mapLoading ? 'Building…' : 'Rebuild map'}
				</button>
				<button class="map-rebuild-btn" onclick={runEnrich} disabled={enriching || mapLoading}>
					{enriching ? 'Enriching…' : 'Enrich metadata'}
				</button>
			</div>
		</div>
		{#if mapVisible}
			<div id="taste-map-panel">
				{#await import('$lib/components/TasteMap.svelte') then module}
					<module.default {mapData} {library} loading={mapLoading} error={mapError} />
				{:catch}
					<p class="state error">Taste map could not be loaded. Rebuild remains available.</p>
				{/await}
			</div>
		{/if}
	</div>
{/if}

<style>
	.library-switch {
		display: inline-flex;
		gap: 4px;
		padding: 4px;
		border: 1px solid var(--line);
		border-radius: 999px;
		background: var(--panel);
		margin-bottom: 16px;
	}
	.library-switch button {
		padding: 7px 12px;
		border-radius: 999px;
		border: none;
		background: transparent;
		color: var(--muted);
	}
	.library-switch button.active {
		background: var(--gold);
		color: var(--on-gold);
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
	.train-cols,
	.detail-columns {
		display: grid;
		gap: 14px;
	}
	.stat-grid {
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		margin-bottom: 16px;
	}
	.train-cols,
	.manager-grid,
	.detail-columns {
		grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
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
	.engine.on {
		border-color: color-mix(in srgb, var(--good) 35%, var(--line));
	}
	.engine-head,
	.panel-head,
	.detail-head,
	.artifact-title,
	.map-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
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
	.artifact-meta,
	.artifact-date,
	.map-stats {
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
	.gauges,
	.genres,
	.map-stats,
	.detail-metrics {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.gauges {
		flex-direction: column;
		margin-top: 12px;
	}
	.gl {
		display: flex;
		justify-content: space-between;
		font-size: 11px;
		color: var(--muted);
		margin-bottom: 4px;
	}
	.g-chip,
	.badge,
	.detail-metrics span {
		display: inline-flex;
		align-items: center;
		padding: 4px 9px;
		border-radius: 999px;
		background: var(--panel2);
		border: 1px solid var(--line);
	}
	.g-chip {
		gap: 6px;
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
	.scope-row {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.scope {
		flex: 1;
		min-width: 130px;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 9px 12px;
		border-radius: 8px;
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--muted);
		text-align: left;
	}
	.scope.on {
		border-color: var(--gold-deep);
		background: var(--gold-soft);
		color: var(--gold);
	}
	.card-note,
	.manager-card p {
		margin: 0;
		color: var(--muted);
		font-size: 12.5px;
		line-height: 1.5;
	}
	.hint {
		font-size: 12px;
		color: var(--warn);
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		border: 1px solid color-mix(in srgb, var(--warn) 22%, transparent);
		border-radius: var(--radius-sm);
		padding: 8px 11px;
	}
	.btn-gold,
	.map-rebuild-btn {
		border: 1px solid var(--line2);
		background: var(--panel2);
		color: var(--text);
		border-radius: 8px;
		padding: 9px 12px;
		font-weight: 600;
	}
	.btn-gold {
		background: var(--gold-soft);
		border-color: var(--gold-deep);
		color: var(--gold);
	}
	.artifact-list,
	.detail-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.detail-list {
		max-height: 360px;
		overflow: auto;
		padding-right: 4px;
	}
	.artifact-row,
	.detail-card,
	.map-section {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel2);
	}
	.artifact-row {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 12px;
		text-align: left;
	}
	.artifact-row.selected {
		border-color: color-mix(in srgb, var(--gold) 30%, var(--line));
	}
	.artifact-main,
	.detail-card {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.detail-card {
		padding: 14px;
	}
	.detail-row {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
	}
	.detail-row:last-child {
		border-bottom: none;
	}
	.detail-empty {
		color: var(--faint);
		font-size: 12px;
		padding: 8px 0;
	}
	.alert-panel {
		padding: 14px 16px;
		margin-top: 16px;
	}
	.alert-row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding-top: 8px;
	}
	.map-section {
		margin-top: 16px;
		padding: 14px;
		background: transparent;
	}
	.map-title {
		font-size: 18px;
		font-weight: 650;
	}
	.map-actions {
		display: flex;
		gap: 8px;
	}
	@media (max-width: 900px) {
		.artifact-row,
		.map-header {
			flex-direction: column;
			align-items: stretch;
		}
	}
</style>
