<script lang="ts">
	import { onDestroy } from 'svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import StatCard from '$lib/components/StatCard.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import StatusDot from '$lib/components/StatusDot.svelte';
	import RunProgress from '$lib/components/RunProgress.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import TasteMap from '$lib/components/TasteMap.svelte';
	import {
		activateLearnedHead,
		activateTasteProfile,
		archiveLearnedHead,
		archiveTasteProfile,
		cancelRetrain,
		deleteLearnedHead,
		deleteTasteProfile,
		deleteTasteProfileExemplar,
		enrichProfile,
		getLearnedHeadDetail,
		getLearnedHeads,
		getTasteMap,
		getTasteProfileDetail,
		getTasteProfileExemplars,
		getTasteProfiles,
		getTasteStatus,
		retrainHead,
		retrainTaste
	} from '$lib/api/taste';
	import { trackJob, type JobProgressDetail } from '$lib/jobs';
	import { toast } from '$lib/toast';
	import type {
		ManagedArtifactSummary,
		ManagedExemplarRow,
		ManagedHeadDetail,
		ManagedProfileDetail,
		TasteMapData,
		TasteSource,
		TasteStatus
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const initialStatus = data.status;
	const initialMapData = data.mapData ?? null;
	const initialProfiles = data.profiles ?? [];
	const initialHeads = data.heads ?? [];

	let status = $state<TasteStatus | null>(initialStatus);
	let mapData = $state<TasteMapData | null>(initialMapData);
	let profiles = $state<ManagedArtifactSummary[]>(initialProfiles);
	let heads = $state<ManagedArtifactSummary[]>(initialHeads);
	let mapLoading = $state(false);
	let mapError = $state<string | null>(null);
	let detailLoading = $state(false);

	let selectedProfileId = $state<string | null>(null);
	let selectedHeadId = $state<string | null>(null);
	let profileDetail = $state<ManagedProfileDetail | null>(null);
	let headDetail = $state<ManagedHeadDetail | null>(null);
	let profileExemplars = $state<ManagedExemplarRow[]>([]);
	const artifactRegistry = $derived(status?.artifact_registry ?? { available: true });
	const registryUnavailable = $derived(artifactRegistry.available === false);

	function preferredArtifactId(rows: ManagedArtifactSummary[], currentId: string | null): string | null {
		if (currentId && rows.some((row) => row.id === currentId)) return currentId;
		return rows.find((row) => row.status === 'active')?.id ?? rows[0]?.id ?? null;
	}

	async function refresh() {
		try {
			const [nextStatus, nextMap, nextProfiles, nextHeads] = await Promise.all([
				getTasteStatus(fetch),
				getTasteMap(fetch).catch(() => mapData),
				getTasteProfiles(fetch).then((value) => value.profiles),
				getLearnedHeads(fetch).then((value) => value.heads)
			]);
			status = nextStatus;
			mapData = nextMap;
			profiles = nextProfiles;
			heads = nextHeads;
			const nextProfileId = preferredArtifactId(nextProfiles, selectedProfileId);
			const nextHeadId = preferredArtifactId(nextHeads, selectedHeadId);
			if (nextProfileId) {
				await loadProfileDetail(nextProfileId);
			} else {
				selectedProfileId = null;
				profileDetail = null;
				profileExemplars = [];
			}
			if (nextHeadId) {
				await loadHeadDetail(nextHeadId);
			} else {
				selectedHeadId = null;
				headDetail = null;
			}
		} catch {
			/* keep stale */
		}
	}

	const SOURCES: { id: TasteSource; label: string; hint: string }[] = [
		{ id: 'training_dir', label: 'Training folder', hint: 'Curated data/training/positive' },
		{ id: 'library', label: 'Library posters', hint: 'Every deployed poster' }
	];
	let source = $state<TasteSource>('training_dir');
	let rebuildDetail = $state<JobProgressDetail>({});
	let rebuildStatus = $state('running');
	let rebuilding = $state(false);
	let rebuildJobId = $state<string | null>(null);
	let stopRebuild: (() => void) | null = null;

	async function startRebuild() {
		if (rebuilding) return;
		rebuilding = true;
		rebuildDetail = {};
		rebuildStatus = 'running';
		try {
			const job = await retrainTaste(fetch, source);
			rebuildJobId = job.job_id;
			toast('Taste rebuild queued', 'info');
			stopRebuild?.();
			stopRebuild = trackJob(
				fetch,
				job.job_id,
				{
					onProgress: ({ status: s, detail }) => {
						rebuildStatus = s;
						rebuildDetail = detail;
					},
					onDone: (j) => {
						rebuilding = false;
						toast(`Taste rebuild ${j.status}`, j.status === 'succeeded' ? 'good' : 'bad');
						void refresh();
					}
				},
				{ eventsUrl: job.events_url }
			);
		} catch (e) {
			rebuilding = false;
			toast(e instanceof Error ? e.message : 'Rebuild failed to start', 'bad');
		}
	}

	async function cancelRebuild() {
		try {
			await cancelRetrain(fetch);
			toast('Cancellation requested', 'info');
		} catch {
			toast('Could not cancel', 'bad');
		}
	}

	let headDetailProgress = $state<JobProgressDetail>({});
	let headStatus = $state('running');
	let headTraining = $state(false);
	let stopHead: (() => void) | null = null;
	const head = $derived(status?.learned_head);
	const headUnit = $derived(head?.mode === 'pairwise' ? 'pairs' : 'labels');
	const ready = $derived(
		!!head &&
			head.activation.movies.have >= head.activation.movies.need &&
			head.activation.labels.have >= head.activation.labels.need
	);

	async function startHead() {
		if (headTraining) return;
		headTraining = true;
		headDetailProgress = {};
		headStatus = 'running';
		try {
			const job = await retrainHead(fetch);
			toast('Key Art Engine training queued', 'info');
			stopHead?.();
			stopHead = trackJob(
				fetch,
				job.job_id,
				{
					onProgress: ({ status: s, detail }) => {
						headStatus = s;
						headDetailProgress = detail;
					},
					onDone: (j) => {
						headTraining = false;
						toast(`Key Art Engine ${j.status}`, j.status === 'succeeded' ? 'good' : 'bad');
						void refresh();
					}
				},
				{ eventsUrl: job.events_url }
			);
		} catch (e) {
			headTraining = false;
			toast(e instanceof Error ? e.message : 'Training failed to start', 'bad');
		}
	}

	async function rebuildMap() {
		if (mapLoading) return;
		mapLoading = true;
		mapError = null;
		try {
			mapData = await getTasteMap(fetch, true);
			toast('Taste map rebuilt', 'good');
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
			mapData = await enrichProfile(fetch);
			toast('Metadata enriched and taste map refreshed', 'good');
			void refresh();
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
				getTasteProfileDetail(fetch, artifactId),
				getTasteProfileExemplars(fetch, artifactId).then((value) => value.exemplars)
			]);
			profileDetail = detail;
			profileExemplars = exemplars;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not load profile details', 'bad');
		} finally {
			detailLoading = false;
		}
	}

	async function loadHeadDetail(artifactId: string) {
		selectedHeadId = artifactId;
		detailLoading = true;
		try {
			headDetail = await getLearnedHeadDetail(fetch, artifactId);
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not load head details', 'bad');
		} finally {
			detailLoading = false;
		}
	}

	async function manageProfile(action: 'activate' | 'archive' | 'delete', artifactId: string) {
		try {
			if (action === 'activate') {
				await activateTasteProfile(fetch, artifactId);
				toast('Taste profile activated', 'good');
			} else if (action === 'archive') {
				await archiveTasteProfile(fetch, artifactId);
				toast('Taste profile archived', 'good');
			} else {
				if (!globalThis.confirm('Delete this archived taste profile permanently?')) return;
				await deleteTasteProfile(fetch, artifactId);
				if (selectedProfileId === artifactId) {
					selectedProfileId = null;
					profileDetail = null;
					profileExemplars = [];
				}
				toast('Taste profile deleted', 'good');
			}
			await refresh();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Profile action failed', 'bad');
		}
	}

	async function manageHead(action: 'activate' | 'archive' | 'delete', artifactId: string) {
		try {
			if (action === 'activate') {
				await activateLearnedHead(fetch, artifactId);
				toast('Learned head activated', 'good');
			} else if (action === 'archive') {
				await archiveLearnedHead(fetch, artifactId);
				toast('Learned head archived', 'good');
			} else {
				if (!globalThis.confirm('Delete this learned head permanently?')) return;
				await deleteLearnedHead(fetch, artifactId);
				if (selectedHeadId === artifactId) {
					selectedHeadId = null;
					headDetail = null;
				}
				toast('Learned head deleted', 'good');
			}
			await refresh();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Head action failed', 'bad');
		}
	}

	async function removeExemplar(name: string) {
		if (!profileDetail || profileDetail.status !== 'active') return;
		if (!globalThis.confirm(`Remove ${name} from the active taste profile?`)) return;
		try {
			await deleteTasteProfileExemplar(fetch, profileDetail.id, name);
			toast('Exemplar removed', 'good');
			mapData = await getTasteMap(fetch);
			await refresh();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not remove exemplar', 'bad');
		}
	}

	function pct(have: number, need: number): number {
		return need > 0 ? Math.min(100, (have / need) * 100) : 100;
	}

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return 'never';
		const t = new Date(iso);
		return Number.isNaN(t.getTime()) ? 'never' : t.toLocaleString();
	}

	function metric(row: ManagedArtifactSummary, key: string): number | string {
		return (row.summary?.[key] as number | string | undefined) ?? 0;
	}

	onDestroy(() => {
		stopRebuild?.();
		stopHead?.();
	});

	$effect(() => {
		if (!selectedProfileId && profiles.length) {
			void loadProfileDetail(preferredArtifactId(profiles, null) ?? profiles[0].id);
		}
	});

	$effect(() => {
		if (!selectedHeadId && heads.length) {
			void loadHeadDetail(preferredArtifactId(heads, null) ?? heads[0].id);
		}
	});
</script>

<SectionHeader title="Key Art Engine" subtitle="Taste profile & learned poster ranker" />

{#if data.error || !status}
	<div class="empty">
		<Icon name="taste" size={34} stroke={1} />
		<strong>Taste status unavailable</strong>
		<span>{data.error ?? 'Could not reach the taste service.'}</span>
	</div>
{:else}
	{#if registryUnavailable}
		<div class="registry-warning">
			<strong>Artifact management unavailable</strong>
			<span>Run <code>alembic upgrade head</code> and restart Marquee to enable taste profile and learned head management.</span>
		</div>
	{/if}

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
		<div class="card engine" class:on={head?.active}>
			<div class="engine-head">
				<span class="label">Key Art Engine</span>
				<span class="engine-state" style="--c:{head?.active ? 'var(--good)' : 'var(--faint)'}">
					<StatusDot tone={head?.active ? 'good' : 'muted'} size={7} />
					{head?.active ? 'Active' : 'Inactive'}
				</span>
			</div>
			<div class="engine-val mono">{head?.n_samples ?? 0}<span class="unit"> {headUnit}</span></div>
			{#if head}
				<div class="gauges">
					<div class="gauge">
						<div class="gl">
							<span>Movies</span><span class="mono">{head.activation.movies.have}/{head.activation.movies.need}</span>
						</div>
						<ProgressBar
							value={pct(head.activation.movies.have, head.activation.movies.need)}
							tone={head.activation.movies.have >= head.activation.movies.need ? 'good' : 'gold'}
							height={5}
						/>
					</div>
					<div class="gauge">
						<div class="gl">
							<span>{head.mode === 'pairwise' ? 'Pairs' : 'Labels'}</span><span class="mono">{head.activation.labels.have}/{head.activation.labels.need}</span>
						</div>
						<ProgressBar
							value={pct(head.activation.labels.have, head.activation.labels.need)}
							tone={head.activation.labels.have >= head.activation.labels.need ? 'good' : 'gold'}
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

	<div class="train-cols">
		<section class="train-card">
			<h3>Initial training</h3>
			<p class="card-note">Rebuild the active taste profile from your curated folder or deployed library posters.</p>
			<div class="scope-row">
				{#each SOURCES as s (s.id)}
					<button class="scope" class:on={source === s.id} disabled={rebuilding} onclick={() => (source = s.id)} title={s.hint}>
						{s.label}
						<small>{s.hint}</small>
					</button>
				{/each}
			</div>
			{#if rebuilding || rebuildJobId}
				<RunProgress
					detail={rebuildDetail}
					status={rebuildStatus}
					title="Rebuilding taste profile"
					onCancel={rebuilding ? cancelRebuild : undefined}
				/>
			{/if}
			<button class="btn-gold" onclick={startRebuild} disabled={rebuilding}>
				{rebuilding ? 'Rebuilding…' : 'Rebuild profile'}
			</button>
		</section>

		<section class="train-card">
			<h3>Train the Key Art Engine</h3>
			<p class="card-note">Picks accumulate labels automatically. Train the learned ranker whenever you want to fold in your latest choices.</p>
			{#if headTraining}
				<RunProgress detail={headDetailProgress} status={headStatus} title="Training Key Art Engine" />
			{/if}
			{#if !ready}
				<div class="hint">Needs more data to activate. Keep approving posters to reach the thresholds above.</div>
			{/if}
			<button class="btn-gold" onclick={startHead} disabled={headTraining || !ready}>
				{headTraining ? 'Training…' : 'Train Key Art Engine'}
			</button>
		</section>
	</div>

	<div class="manager-grid">
		<section class="manager-card">
			<div class="panel-head">
				<div>
					<h3>Taste profiles</h3>
					<p>Inspect snapshots, activate older profiles, and clean up duplicate exemplars.</p>
				</div>
			</div>
			<div class="artifact-list">
				{#each profiles as profile (profile.id)}
					<div class="artifact-row" class:selected={selectedProfileId === profile.id} onclick={() => loadProfileDetail(profile.id)} onkeydown={(event) => event.key === 'Enter' && loadProfileDetail(profile.id)} tabindex="0" role="button">
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
						<div class="artifact-actions">
							<button class="mini-btn" onclick={(event) => { event.stopPropagation(); manageProfile('activate', profile.id); }} disabled={profile.status === 'active'}>Activate</button>
							<button class="mini-btn" onclick={(event) => { event.stopPropagation(); manageProfile('archive', profile.id); }} disabled={profile.status !== 'active'}>Archive</button>
							<button class="mini-btn danger" onclick={(event) => { event.stopPropagation(); manageProfile('delete', profile.id); }} disabled={profile.status === 'active'}>Delete</button>
						</div>
					</div>
				{/each}
				{#if profiles.length === 0}
					<div class="detail-empty">
						{registryUnavailable
							? 'Artifact registry is waiting for the database migration.'
							: 'No managed taste profiles yet.'}
					</div>
				{/if}
			</div>

			{#if detailLoading && selectedProfileId}
				<div class="detail-empty">Loading profile details…</div>
			{:else if profileDetail}
				<div class="detail-card">
					<div class="detail-head">
						<div>
							<h4>{profileDetail.label}</h4>
							<div class="artifact-date">Created {fmtDate(profileDetail.created_at)} · Activated {fmtDate(profileDetail.activated_at)}</div>
						</div>
						<span class="badge" class:active={profileDetail.status === 'active'}>{profileDetail.status}</span>
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
						<h5>Exemplar cleanup</h5>
						<div class="detail-list exemplars">
							{#each profileExemplars as exemplar (exemplar.name)}
								<div class="detail-row exemplar-row">
									<span>{exemplar.title}{exemplar.year ? ` (${exemplar.year})` : ''}{exemplar.is_duplicate ? ` · dup x${exemplar.duplicate_count}` : ''}</span>
									<button class="mini-btn danger" onclick={() => removeExemplar(exemplar.name)} disabled={profileDetail.status !== 'active'}>Remove</button>
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
					<h3>Learned heads</h3>
					<p>View training snapshots, top features, and switch the active learned ranker.</p>
				</div>
			</div>
			<div class="artifact-list">
				{#each heads as managedHead (managedHead.id)}
					<div class="artifact-row" class:selected={selectedHeadId === managedHead.id} onclick={() => loadHeadDetail(managedHead.id)} onkeydown={(event) => event.key === 'Enter' && loadHeadDetail(managedHead.id)} tabindex="0" role="button">
						<div class="artifact-main">
							<div class="artifact-title">
								<strong>{managedHead.label}</strong>
								<span class="badge" class:active={managedHead.status === 'active'}>
									{managedHead.status}
								</span>
							</div>
							<div class="artifact-meta">
								<span>{managedHead.summary.sample_count ?? 0} samples</span>
								<span>{managedHead.summary.unique_movies ?? 0} movies</span>
								<span>{managedHead.summary.mode ?? 'unknown mode'}</span>
							</div>
							<div class="artifact-date">Trained {fmtDate(managedHead.trained_at)}</div>
						</div>
						<div class="artifact-actions">
							<button class="mini-btn" onclick={(event) => { event.stopPropagation(); manageHead('activate', managedHead.id); }} disabled={managedHead.status === 'active'}>Activate</button>
							<button class="mini-btn" onclick={(event) => { event.stopPropagation(); manageHead('archive', managedHead.id); }} disabled={managedHead.status !== 'active'}>Archive</button>
							<button class="mini-btn danger" onclick={(event) => { event.stopPropagation(); manageHead('delete', managedHead.id); }}>Delete</button>
						</div>
					</div>
				{/each}
				{#if heads.length === 0}
					<div class="detail-empty">
						{registryUnavailable
							? 'Artifact registry is waiting for the database migration.'
							: 'No managed learned heads yet.'}
					</div>
				{/if}
			</div>

			{#if detailLoading && selectedHeadId}
				<div class="detail-empty">Loading learned head details…</div>
			{:else if headDetail}
				<div class="detail-card">
					<div class="detail-head">
						<div>
							<h4>{headDetail.label}</h4>
							<div class="artifact-date">Created {fmtDate(headDetail.created_at)} · Trained {fmtDate(headDetail.trained_at)}</div>
						</div>
						<span class="badge" class:active={headDetail.status === 'active'}>{headDetail.status}</span>
					</div>
					<div class="detail-metrics">
						<span>{headDetail.summary.sample_count ?? 0} samples</span>
						<span>{headDetail.summary.unique_movies ?? 0} movies</span>
						<span>{((headDetail.summary.train_accuracy as number | undefined) ?? 0).toFixed(3)} accuracy</span>
					</div>
					<div class="detail-columns">
						<div>
							<h5>Top weighted features</h5>
							<div class="detail-list">
								{#each (headDetail.summary.top_features ?? []).slice(0, 10) as feature (feature.name)}
									<div class="detail-row">
										<span>{feature.name}</span>
										<span class="mono">{feature.weight.toFixed(3)}</span>
									</div>
								{/each}
							</div>
						</div>
						<div>
							<h5>Contributing movies</h5>
							<div class="detail-list">
								{#each headDetail.movies as movie (`${movie.movie_id}-${movie.title}`)}
									<div class="detail-row">
										<span>{movie.title}{movie.year ? ` (${movie.year})` : ''}</span>
										<span class="mono">{movie.contribution_count}</span>
									</div>
								{/each}
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
				<button class="map-rebuild-btn" onclick={rebuildMap} disabled={mapLoading}>
					{mapLoading ? 'Building…' : 'Rebuild map'}
				</button>
				<button class="map-rebuild-btn" onclick={runEnrich} disabled={enriching || mapLoading}>
					{enriching ? 'Enriching…' : 'Enrich metadata'}
				</button>
			</div>
		</div>
		<TasteMap {mapData} loading={mapLoading} error={mapError} />
	</div>
{/if}

<style>
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
	.alert-panel,
	.registry-warning {
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.registry-warning {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding: 12px 14px;
		margin-bottom: 16px;
		border-color: color-mix(in srgb, var(--warn) 28%, var(--line));
		background: color-mix(in srgb, var(--warn) 8%, var(--panel));
		color: var(--muted);
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
	.scope-row,
	.artifact-actions {
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
	.map-rebuild-btn,
	.mini-btn {
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
	.mini-btn {
		padding: 6px 10px;
		font-size: 12px;
	}
	.mini-btn.danger {
		color: var(--bad);
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
	.exemplar-row button {
		flex-shrink: 0;
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
