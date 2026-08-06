<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import PosterLibraryToggle from '$lib/components/PosterLibraryToggle.svelte';
	import TabBar from '$lib/components/TabBar.svelte';
	import { getRankingResidualDetail, getTasteProfileDetail } from '$lib/api/taste';
	import { assetBreakdown, subjectNounTitle } from '$lib/taste/library-copy';
	import { toast } from '$lib/toast';
	import type {
		ManagedArtifactSummary,
		ManagedResidualDetail,
		ManagedProfileDetail
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const library = $derived<'movies' | 'tv'>(data.library ?? 'movies');

	type Kind = 'profiles' | 'residuals';
	let kind = $state<Kind>('profiles');

	// Read straight off `load` so the server renders the real rails rather than an
	// empty state that only fills in after hydration.
	const profiles = $derived<ManagedArtifactSummary[]>(data.profiles ?? []);
	const residuals = $derived<ManagedArtifactSummary[]>(data.residuals ?? []);

	let selectedProfileId = $state<string | null>(null);
	let selectedResidualId = $state<string | null>(null);
	let profileDetail = $state<ManagedProfileDetail | null>(null);
	let residualDetail = $state<ManagedResidualDetail | null>(null);
	let detailLoading = $state(false);
	// SvelteSet is reactive on its own; it is cleared rather than replaced.
	const expandedSubjects = new SvelteSet<string>();

	$effect(() => {
		void data;
		selectedProfileId = null;
		selectedResidualId = null;
		profileDetail = null;
		residualDetail = null;
		expandedSubjects.clear();
	});

	function preferredArtifactId(rows: ManagedArtifactSummary[]): string | null {
		return rows.find((row) => row.status === 'active')?.id ?? rows[0]?.id ?? null;
	}

	$effect(() => {
		if (!selectedProfileId && profiles.length) {
			void loadProfileDetail(preferredArtifactId(profiles) ?? profiles[0].id);
		}
	});

	$effect(() => {
		if (!selectedResidualId && residuals.length) {
			void loadResidualDetail(preferredArtifactId(residuals) ?? residuals[0].id);
		}
	});

	async function loadProfileDetail(artifactId: string) {
		selectedProfileId = artifactId;
		detailLoading = true;
		try {
			// The detail payload carries every poster per subject, so the flat
			// /exemplars endpoint is not a second round trip the page needs.
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

	function fmtDate(iso: string | null | undefined): string {
		if (!iso) return 'never';
		const t = new Date(iso);
		return Number.isNaN(t.getTime()) ? 'never' : t.toLocaleString();
	}

	function titleOf(title: string, year: number | null | undefined): string {
		return year ? `${title} (${year})` : title;
	}

	const rows = $derived(kind === 'profiles' ? profiles : residuals);
	const selectedId = $derived(kind === 'profiles' ? selectedProfileId : selectedResidualId);
	const select = $derived(kind === 'profiles' ? loadProfileDetail : loadResidualDetail);
</script>

<header class="scope">
	<PosterLibraryToggle
		active={library === 'tv' ? 'television' : 'films'}
		films="/taste/history?library=movies"
		television="/taste/history?library=tv"
		label="Taste library"
	/>
	<TabBar
		tabs={[
			{ id: 'profiles', label: 'Taste Profiles', count: profiles.length },
			{ id: 'residuals', label: 'Ranking Residuals', count: residuals.length }
		]}
		active={kind}
		onSelect={(id) => (kind = id as Kind)}
	/>
</header>

<h1 class="sr-only">Build History</h1>

<div class="manager">
	<div class="rail" role="listbox" aria-label="Generations" tabindex="-1">
		{#each rows as row (row.id)}
			<button
				class="rail-row"
				class:selected={selectedId === row.id}
				role="option"
				aria-selected={selectedId === row.id}
				onclick={() => select(row.id)}
			>
				<span class="rail-title">
					<strong>{row.label}</strong>
					<span class="badge" class:active={row.status === 'active'}>{row.status}</span>
				</span>
				<span class="rail-meta">
					{#if kind === 'profiles'}
						{row.summary.exemplars ?? 0} exemplars
					{:else}
						{row.summary.evaluation?.pair_count ?? 0} pairs
					{/if}
				</span>
				<span class="rail-meta">{fmtDate(row.updated_at ?? row.trained_at)}</span>
			</button>
		{/each}
		{#if rows.length === 0}
			<div class="empty">Nothing built yet.</div>
		{/if}
	</div>

	<div class="detail">
		{#if kind === 'profiles'}
			{#if detailLoading && selectedProfileId && !profileDetail}
				<div class="empty">Loading…</div>
			{:else if profileDetail}
				<div class="detail-head">
					<div>
						<h2>{profileDetail.label}</h2>
						<div class="stamp">
							Created {fmtDate(profileDetail.created_at)} · Activated {fmtDate(
								profileDetail.activated_at
							)} · {profileDetail.source_mode ?? 'unknown source'}
						</div>
					</div>
					<span class="badge" class:active={profileDetail.status === 'active'}>
						{profileDetail.status}
					</span>
				</div>
				<div class="metrics">
					<div>
						<span>Exemplars</span><b class="mono">{profileDetail.summary.exemplars ?? 0}</b>
					</div>
					<div>
						<span>{subjectNounTitle(library)}</span>
						<b class="mono">
							{profileDetail.summary.unique_subjects ?? profileDetail.summary.unique_movies ?? 0}
						</b>
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

				<h3>Contributing {subjectNounTitle(library)}</h3>
				<div class="list">
					{#each profileDetail.movies as subject (subject.subject_key)}
						<div class="subject">
							<button
								class="subject-row"
								aria-expanded={expandedSubjects.has(subject.subject_key)}
								onclick={() => toggleSubject(subject.subject_key)}
							>
								<span class="chev" aria-hidden="true">
									{expandedSubjects.has(subject.subject_key) ? '▾' : '▸'}
								</span>
								<span class="s-title">{titleOf(subject.title, subject.year)}</span>
								<span class="s-assets">
									{subject.asset_summary || assetBreakdown(subject.asset_counts ?? {})}
								</span>
								<span class="mono s-count">{subject.contribution_count}</span>
							</button>
							{#if expandedSubjects.has(subject.subject_key)}
								<div class="assets">
									{#each subject.assets ?? [] as asset (asset.name)}
										<div class="asset">
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
						<div class="empty">This profile has no exemplars.</div>
					{/if}
				</div>

				{#if profileDetail.duplicate_groups.length}
					<h3>Duplicate Groups</h3>
					<div class="list short">
						{#each profileDetail.duplicate_groups as group (`${group.label}-${group.season_number}`)}
							<div class="row">
								<span>{group.label}</span>
								<span class="mono">×{group.count}</span>
							</div>
						{/each}
					</div>
				{/if}
			{:else if profiles.length}
				<div class="empty">Select a generation to inspect it.</div>
			{/if}
		{:else if detailLoading && selectedResidualId && !residualDetail}
			<div class="empty">Loading…</div>
		{:else if residualDetail}
			<div class="detail-head">
				<div>
					<h2>{residualDetail.label}</h2>
					<div class="stamp">
						Created {fmtDate(residualDetail.created_at)} · Trained {fmtDate(
							residualDetail.trained_at
						)}
					</div>
				</div>
				<span class="badge" class:active={residualDetail.status === 'active'}>
					{residualDetail.status}
				</span>
			</div>
			<div class="metrics">
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
					<b class="mono">
						{((residualDetail.summary.evaluation?.improvement as number | undefined) ?? 0).toFixed(
							3
						)}
					</b>
				</div>
				<div>
					<span>Alpha / max Δ</span>
					<b class="mono">
						{residualDetail.summary.alpha ?? 0} / {residualDetail.summary.delta_max ?? 0}
					</b>
				</div>
			</div>
			<h3>Top Residual Features</h3>
			<div class="list">
				{#each (residualDetail.summary.top_features ?? []).slice(0, 10) as feature (feature.name)}
					<div class="row">
						<span>{feature.name}</span>
						<span class="mono">{feature.weight.toFixed(3)}</span>
					</div>
				{/each}
			</div>
		{:else if residuals.length}
			<div class="empty">Select a generation to inspect it.</div>
		{/if}
	</div>
</div>

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

	/* The generation list is a rail beside the detail, not a stack above it — the
	   detail is what people came for and it used to start below the fold. */
	.manager {
		display: grid;
		grid-template-columns: 230px minmax(0, 1fr);
		gap: 18px;
		align-items: start;
	}
	.rail {
		display: flex;
		flex-direction: column;
		gap: 6px;
		max-height: calc(100vh - 220px);
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
		background: var(--panel);
		color: var(--text);
	}
	.rail-row:hover {
		border-color: var(--line2);
	}
	.rail-row.selected {
		border-color: color-mix(in srgb, var(--gold) 45%, var(--line));
		background: color-mix(in srgb, var(--gold) 7%, var(--panel));
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

	.detail {
		display: flex;
		flex-direction: column;
		gap: 14px;
		min-width: 0;
		padding: 18px 20px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius);
	}
	.detail-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
	}
	.detail h2 {
		margin: 0;
		font-size: 15px;
		font-weight: 650;
	}
	.stamp {
		margin-top: 3px;
		font-size: 12px;
		color: var(--muted);
	}
	.badge {
		display: inline-flex;
		align-items: center;
		padding: 4px 9px;
		border-radius: var(--radius-pill);
		background: var(--panel2);
		border: 1px solid var(--line);
		font-size: 12px;
	}
	.badge.active {
		background: color-mix(in srgb, var(--good) 12%, var(--panel2));
		border-color: color-mix(in srgb, var(--good) 35%, var(--line));
		color: var(--good);
	}

	/* Label over value, so four numbers can be compared at a glance. */
	.metrics {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
		gap: 1px;
		background: var(--line);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}
	.metrics div {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 9px 11px;
		background: var(--panel2);
	}
	.metrics span {
		font-size: 10.5px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
	}
	.metrics b {
		font-size: 15px;
		font-weight: 600;
	}

	h3 {
		margin: 4px 0 0;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint);
	}
	.list {
		display: flex;
		flex-direction: column;
		max-height: 380px;
		overflow: auto;
		padding-right: 4px;
	}
	.list.short {
		max-height: 180px;
	}
	.row {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		padding: 8px 0;
		border-bottom: 1px solid var(--line);
		font-size: 13px;
	}
	.row:last-child {
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
		color: var(--gold-copy);
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
	.s-assets,
	.s-count {
		font-size: 11.5px;
		color: var(--muted);
	}
	.assets {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 0 0 10px 24px;
	}
	.asset {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
		color: var(--muted);
	}
	.dup {
		color: var(--warn-copy);
		font-size: 11px;
	}
	.empty {
		color: var(--faint);
		font-size: 12px;
		padding: 8px 0;
	}
	.mono {
		font-family: var(--font-mono);
	}

	@media (max-width: 900px) {
		.manager {
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
