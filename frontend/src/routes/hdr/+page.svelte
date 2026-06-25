<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { analyzeDoviBatch, putRadarrOverlayPreferences } from '$lib/api/radarr-overlay';
	import { trackJob } from '$lib/jobs';
	import HdrBadge from '$lib/components/HdrBadge.svelte';
	import { toast } from '$lib/toast';
	import type {
		HdrKind,
		HdrPreferenceChoice,
		RadarrOverlayItem,
		RadarrOverlayProfilePreference,
		RadarrOverlayStatus
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let currentPage = $derived(data.data?.page ?? 1);
	let pageSize = $derived(data.data?.page_size ?? 100);
	let totalItems = $derived(data.data?.total ?? 0);
	let totalPages = $derived(Math.ceil(totalItems / pageSize));
	let startIndex = $derived(totalItems > 0 ? (currentPage - 1) * pageSize + 1 : 0);
	let endIndex = $derived(Math.min(currentPage * pageSize, totalItems));

	const TAGS: (HdrKind | 'unknown')[] = [
		'sdr',
		'hdr',
		'hdr10',
		'hdr10p',
		'dovi',
		'dovi_no_fallback'
	];
	const STATUS_META: Record<RadarrOverlayStatus, { label: string; tone: string; note: string }> = {
		below_target: {
			label: 'Below target',
			tone: 'var(--bad)',
			note: 'Current file does not satisfy the configured meet target.'
		},
		meets_target: {
			label: 'Meets target',
			tone: 'var(--good)',
			note: 'Current file satisfies the configured meet target.'
		},
		exceeds_target: {
			label: 'Exceeds target',
			tone: 'var(--gold)',
			note: 'Current file satisfies the configured exceed target.'
		},
		no_hdr_target: {
			label: 'No HDR target',
			tone: 'var(--faint)',
			note: 'This quality profile does not actively target HDR formats.'
		}
	};
	const TAG_LABEL: Record<string, string> = {
		sdr: 'SDR',
		hdr: 'HDR',
		hdr10: 'HDR10',
		hdr10p: 'HDR10+',
		dovi: 'DoVi',
		dovi_no_fallback: 'DoVi w/o fallback'
	};
	const DIST_COLORS: Record<string, string> = {
		sdr: 'var(--faint)',
		hdr: 'var(--good)',
		hdr10: 'var(--info)',
		hdr10p: 'var(--dovi)',
		dovi: 'var(--gold)',
		dovi_no_fallback: 'var(--bad)'
	};
	const DOVI_P8_VARIANT: Record<number, string> = {
		1: 'P8.1',
		2: 'P8.2',
		4: 'P8.4'
	};
	const PREFERENCE_LABEL: Record<HdrPreferenceChoice, string> = {
		sdr: 'SDR',
		hdr: 'HDR',
		hdr10: 'HDR10',
		hdr10p: 'HDR10+',
		dovi_no_fallback: 'DoVi w/o fallback',
		dovi_fallback: 'DoVi'
	};
	const DEFAULT_SORT_DIR: Record<'title' | 'cf_score' | 'preference_status', 'asc' | 'desc'> = {
		title: 'asc',
		cf_score: 'desc',
		preference_status: 'desc'
	};
	const PREFERENCE_RANK: Record<HdrPreferenceChoice, number> = {
		sdr: -1,
		hdr: 0,
		hdr10: 1,
		hdr10p: 2,
		dovi_no_fallback: 3,
		dovi_fallback: 4
	};

	type PreferenceDraft = RadarrOverlayProfilePreference;

	let preferenceDrafts = $state<PreferenceDraft[]>([]);
	let savingPreferences = $state(false);

	$effect(() => {
		const prefs = data.data?.profile_preferences ?? [];
		preferenceDrafts = prefs.map((preference) => ({
			...preference
		}));

		// Initialize excludedKeys from backend excluded_targets
		const nextKeys: Record<string, boolean> = {};
		for (const pref of prefs) {
			for (const choice of pref.excluded_targets ?? []) {
				nextKeys[excludedKey(pref, choice)] = true;
			}
		}
		excludedKeys = nextKeys;
	});

	function selectedTags(): string[] {
		const raw = page.url.searchParams.getAll('hdr_tags');
		if (!raw.length) return [];
		return raw
			.flatMap((value) => value.split(','))
			.map((value) => value.trim())
			.filter(Boolean);
	}

	function withParams(patch: Record<string, string | null>, tagsOverride?: string[]): string {
		const sp = new URLSearchParams(page.url.searchParams);
		if (tagsOverride) {
			sp.delete('hdr_tags');
			for (const tag of tagsOverride) sp.append('hdr_tags', tag);
		}
		for (const [key, value] of Object.entries(patch)) {
			if (value === null || value === '') sp.delete(key);
			else sp.set(key, value);
		}
		if (!('page' in patch)) {
			sp.delete('page');
		}
		const query = sp.toString();
		return query ? `/hdr?${query}` : '/hdr';
	}

	function toggleTagHref(tag: string): string {
		const current = new Set(selectedTags());
		if (current.has(tag)) current.delete(tag);
		else current.add(tag);
		return withParams({}, [...current]);
	}

	function clearHref(): string {
		return '/hdr';
	}

	function pct(count: number, total: number): string {
		if (!total || count <= 0) return '0%';
		return `${Math.max(4, (count / total) * 100)}%`;
	}

	function sortHref(sortBy: 'title' | 'cf_score' | 'preference_status'): string {
		const currentSort =
			(page.url.searchParams.get('sort_by') as typeof sortBy | null) ?? 'cf_score';
		const currentDir = (page.url.searchParams.get('sort_dir') as 'asc' | 'desc' | null) ?? 'desc';
		const nextDir =
			currentSort === sortBy ? (currentDir === 'asc' ? 'desc' : 'asc') : DEFAULT_SORT_DIR[sortBy];
		return withParams({ sort_by: sortBy, sort_dir: nextDir });
	}

	function sortGlyph(sortBy: 'title' | 'cf_score' | 'preference_status'): string {
		const currentSort = page.url.searchParams.get('sort_by') ?? 'cf_score';
		if (currentSort !== sortBy) return '';
		return (page.url.searchParams.get('sort_dir') ?? 'desc') === 'asc' ? '↑' : '↓';
	}

	function hdrOnlyKinds(kinds: HdrKind[]): HdrKind[] {
		return kinds.filter((kind) => kind !== 'dovi' && kind !== 'dovi_no_fallback');
	}

	function primaryHdrKind(kind: HdrKind | null): HdrKind | null {
		return kind === 'dovi' || kind === 'dovi_no_fallback' ? null : kind;
	}

	function doviBadgeLabel(item: RadarrOverlayItem): string | null {
		if (!item.hdr_tags.includes('dovi') && !item.dovi_no_fallback) return null;
		if (item.dovi_profile == null) return 'DoVi P?';
		const profile =
			item.dovi_profile === 8
				? (item.dovi_bl_signal_compatibility_id != null
						? DOVI_P8_VARIANT[item.dovi_bl_signal_compatibility_id]
						: null) ?? 'P8'
				: `P${item.dovi_profile}`;
		const suffix = item.dovi_profile === 7 && item.dovi_el_type ? ` ${item.dovi_el_type}` : '';
		return `DoVi ${profile}${suffix}`;
	}

	function doviBadgeTone(item: RadarrOverlayItem): string {
		if (item.dovi_profile == null) return 'var(--low)';
		return item.dovi_no_fallback ? 'var(--bad)' : 'var(--gold)';
	}

	function doviBadgeTitle(item: RadarrOverlayItem): string | null {
		const label = doviBadgeLabel(item);
		if (!label) return null;
		if (item.dovi_profile == null) {
			return `${label}. Dolby Vision is present, but this file has not been analyzed yet.`;
		}
		if (item.dovi_profile === 5) return `${label}. Profile 5 can show green/purple tint on non-DV playback.`;
		if (item.dovi_el_type === 'FEL') return `${label}. Full enhancement layer can trigger playback issues.`;
		if (item.dovi_el_type === 'MEL') return `${label}. Minimal enhancement layer is generally safe to drop.`;
		return label;
	}

	// ── Tier ladder per draft ────────────────────────────────────────────
	let expandedDrafts = $state<Record<number, boolean>>({});
	// Excluded tiers keyed by "profile_id:choice" for deep reactivity
	let excludedKeys = $state<Record<string, boolean>>({});

	function excludedKey(draft: PreferenceDraft, choice: string): string {
		return `${draft.profile_id}:${choice}`;
	}

	function isExcluded(draft: PreferenceDraft, choice: string): boolean {
		return !!excludedKeys[excludedKey(draft, choice)];
	}

	function effectiveChoices(draft: PreferenceDraft): HdrPreferenceChoice[] {
		return draft.available_preference_targets.filter((choice) => !isExcluded(draft, choice));
	}

	function findMeetFromChoices(choices: HdrPreferenceChoice[]): HdrPreferenceChoice | null {
		return choices.length > 0 ? choices[0] : null;
	}

	function excludedCount(draft: PreferenceDraft): number {
		let count = 0;
		for (const choice of draft.available_preference_targets) {
			if (isExcluded(draft, choice)) count++;
		}
		return count;
	}

	function toggleMeetBoundary(draft: PreferenceDraft, choice: HdrPreferenceChoice): void {
		if (draft.meet_target === choice) {
			draft.meet_target = null;
		} else {
			draft.meet_target = choice;
			// If exceeds target is set but is lower or equal, clear/reset it
			if (
				draft.exceed_target &&
				PREFERENCE_RANK[draft.exceed_target] <= PREFERENCE_RANK[choice]
			) {
				draft.exceed_target = null;
			}
		}
	}

	function toggleExceedBoundary(draft: PreferenceDraft, choice: HdrPreferenceChoice): void {
		if (draft.exceed_target === choice) {
			draft.exceed_target = null;
		} else {
			draft.exceed_target = choice;
			// If meets target is set but is higher or equal, clear/reset it
			if (
				draft.meet_target &&
				PREFERENCE_RANK[draft.meet_target] >= PREFERENCE_RANK[choice]
			) {
				draft.meet_target = null;
			}
		}
	}

	function zoneForChoice(
		draft: PreferenceDraft,
		choice: HdrPreferenceChoice
	): 'exceed' | 'meet' | 'fail' {
		const rank = PREFERENCE_RANK[choice];
		const meetRank = draft.meet_target ? PREFERENCE_RANK[draft.meet_target] : -1;
		const exceedRank = draft.exceed_target ? PREFERENCE_RANK[draft.exceed_target] : -1;

		if (exceedRank >= 0 && rank >= exceedRank) {
			return 'exceed';
		}
		if (meetRank >= 0 && rank >= meetRank) {
			return 'meet';
		}
		if (draft.meet_target == null && exceedRank >= 0 && rank < exceedRank) {
			return 'meet';
		}
		if (draft.meet_target == null && draft.exceed_target == null) {
			return 'meet';
		}
		return 'fail';
	}

	function dismissRung(draft: PreferenceDraft, choice: HdrPreferenceChoice): void {
		excludedKeys[excludedKey(draft, choice)] = true;

		if (draft.meet_target === choice) {
			draft.meet_target = null;
		}
		if (draft.exceed_target === choice) {
			draft.exceed_target = null;
		}
	}

	function restoreRung(draft: PreferenceDraft, choice: HdrPreferenceChoice): void {
		delete excludedKeys[excludedKey(draft, choice)];
	}

	function collapseDraft(draft: PreferenceDraft): void {
		const next = { ...expandedDrafts };
		next[draft.profile_id] = false;
		expandedDrafts = next;
	}

	function expandDraft(draft: PreferenceDraft): void {
		expandedDrafts = { ...expandedDrafts, [draft.profile_id]: true };
	}

	async function savePreferences(): Promise<void> {
		if (!preferenceDrafts.length) return;
		savingPreferences = true;
		try {
			await putRadarrOverlayPreferences(
				fetch,
				preferenceDrafts.map((draft) => ({
					profile_id: draft.profile_id,
					meet_target: draft.meet_target,
					exceed_target: draft.exceed_target,
					excluded_targets: draft.available_preference_targets.filter((choice) =>
						isExcluded(draft, choice)
					)
				}))
			);
			toast('Overlay preferences saved', 'good');
			await goto(page.url, {
				replaceState: true,
				noScroll: true,
				keepFocus: true,
				invalidateAll: true
			});
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Could not save overlay preferences', 'bad');
		} finally {
			savingPreferences = false;
		}
	}

	// ── Analyze DoVi (batch over Radarr-known Dolby Vision titles) ────────
	let analyzing = $state(false);
	let analyzePercent = $state(0);
	let analyzeMessage = $state('');
	let stopAnalyze: (() => void) | null = null;

	async function analyzeAllDovi() {
		if (analyzing) return;
		analyzing = true;
		analyzePercent = 0;
		analyzeMessage = 'Queuing Dolby Vision analysis…';
		try {
			const { job_id, total } = await analyzeDoviBatch(fetch);
			analyzeMessage = `Analyzing ${total} Dolby Vision ${total === 1 ? 'title' : 'titles'}…`;
			stopAnalyze = trackJob(
				fetch,
				job_id,
				{
					onProgress: ({ detail }) => {
						const d = detail as { percent?: number; message?: string };
						if (typeof d.percent === 'number') analyzePercent = d.percent;
						if (typeof d.message === 'string') analyzeMessage = d.message;
					},
					onDone: async (job) => {
						stopAnalyze = null;
						analyzing = false;
						analyzePercent = 100;
						if (job.status === 'succeeded') {
							toast('Dolby Vision analysis complete', 'good');
							await goto(page.url, { replaceState: true, noScroll: true, invalidateAll: true });
						} else {
							toast(`Analysis ${job.status}`, 'bad');
						}
					},
					onError: () => toast('Analysis progress stream interrupted', 'bad')
				},
				{ eventsUrl: `/api/jobs/${job_id}/events` }
			);
		} catch (e) {
			analyzing = false;
			toast(e instanceof Error ? e.message : 'Could not start Dolby Vision analysis', 'bad');
		}
	}

	onDestroy(() => stopAnalyze?.());
</script>

{#if data.error || !data.data}
	<section class="page">
		<div class="hero">
			<div>
				<p class="eyebrow">Toolbox</p>
				<h1>HDR/DoVi Management</h1>
				<p class="lede">
					Read-only Radarr metadata coverage for HDR, quality profiles, and custom-format scoring.
				</p>
			</div>
		</div>
		<div class="error">{data.error ?? 'Failed to load Radarr overlay.'}</div>
	</section>
{:else}
	<section class="page">
		<div class="hero">
			<div>
				<p class="eyebrow">Toolbox</p>
				<h1>HDR/DoVi Management</h1>
				<p class="lede">
					A consolidated readout of HDR truth, custom-format score posture, and per-profile
					preference compliance.
				</p>
			</div>
			<div class="hero-side">
				<div class="stats">
					<div class="stat">
						<span class="label">Movies</span>
						<strong>{data.data.total}</strong>
					</div>
					<div class="stat">
						<span class="label">Profiles</span>
						<strong>{data.data.profile_preferences.length}</strong>
					</div>
					<div class="stat">
						<span class="label">DoVi no fallback</span>
						<strong>{data.data.distribution.dovi_no_fallback}</strong>
					</div>
				</div>
				<button class="analyze-btn" onclick={analyzeAllDovi} disabled={analyzing}>
					{analyzing ? 'Analyzing…' : 'Analyze DoVi'}
				</button>
			</div>
		</div>

		{#if analyzing}
			<div class="analyze-banner">
				<div class="analyze-bar"><span style={`width:${Math.max(4, analyzePercent)}%`}></span></div>
				<small>{analyzeMessage}</small>
			</div>
		{/if}

		<div class="panel">
			<div class="bar-head">
				<div>
					<h2>HDR distribution</h2>
					<p>
						Each movie contributes to every HDR profile it actually carries, so hybrid files like <code
							>DV HDR10</code
						> count in both groups.
					</p>
				</div>
				<a class="clear" href={clearHref()}>Clear filters</a>
			</div>
			<div class="dist-bar">
				{#each data.data.distribution_order as key (key)}
					{@const count = data.data.distribution[key as keyof typeof data.data.distribution]}
					<a
						class="segment"
						class:active={selectedTags().includes(key)}
						href={toggleTagHref(key)}
						style={`--w:${pct(count, Math.max(data.data.total, 1))};--c:${DIST_COLORS[key] ?? 'var(--faint)'}`}
					>
						<span>{TAG_LABEL[key] ?? key}</span>
						<strong>{count}</strong>
					</a>
				{/each}
			</div>
		</div>

		<form class="panel filters" method="GET">
			<input type="hidden" name="sort_by" value={data.query.sort_by ?? 'cf_score'} />
			<input type="hidden" name="sort_dir" value={data.query.sort_dir ?? 'desc'} />
			<div class="filter-row">
				<label>
					<span>Profile</span>
					<select name="profile_id">
						<option value="">All profiles</option>
						{#each data.data.profiles as profile (profile.id)}
							<option
								value={profile.id}
								selected={String(data.query.profile_id ?? '') === String(profile.id)}
							>
								{profile.name}
							</option>
						{/each}
					</select>
				</label>
				<label>
					<span>Status</span>
					<select name="preference_status">
						<option value="">Any status</option>
						{#each Object.entries(STATUS_META) as [value, meta] (value)}
							<option {value} selected={data.query.preference_status === value}>{meta.label}</option
							>
						{/each}
					</select>
				</label>
				<label>
					<span>Min CF score</span>
					<input name="cf_score_min" type="number" value={data.query.cf_score_min ?? ''} />
				</label>
				<label>
					<span>Max CF score</span>
					<input name="cf_score_max" type="number" value={data.query.cf_score_max ?? ''} />
				</label>
			</div>

			<div class="filter-row tags">
				<div class="tag-group">
					<span class="group-label">HDR profiles</span>
					<div class="checks">
						{#each TAGS as tag (tag)}
							<label class="check">
								<input
									type="checkbox"
									name="hdr_tags"
									value={tag}
									checked={selectedTags().includes(tag)}
								/>
								<span>{TAG_LABEL[tag]}</span>
							</label>
						{/each}
					</div>
				</div>
				<button class="apply" type="submit">Apply filters</button>
			</div>
		</form>

		<div class="panel preference-panel">
			<div class="table-head">
				<div>
					<h2>Preference targets</h2>
					<p>
						Each profile defines what counts as <strong>meet</strong> and optionally
						<strong>exceed</strong>. Below target is derived automatically.
					</p>
				</div>
				<button class="save" type="button" onclick={savePreferences} disabled={savingPreferences}>
					{savingPreferences ? 'Saving…' : 'Save preferences'}
				</button>
			</div>
			{#if preferenceDrafts.length}
				<div class="preference-grid">
					{#each preferenceDrafts as draft (draft.profile_id)}
						<div class="pref-card">
							<div class="pref-head">
								<strong>{draft.profile_name}</strong>
								<span class="pref-label">Radarr targets</span>
							</div>
							<div class="pref-tags">
								<HdrBadge kinds={draft.profile_targets} />
							</div>
							<div class="pref-controls">
								{#if expandedDrafts[draft.profile_id] || (draft.meet_target == null && draft.exceed_target == null)}
									<div class="ladder">
									{#if true}
											{@const activeChoices = draft.available_preference_targets.filter(
												(choice) => !isExcluded(draft, choice)
											)}
											{@const excludedChoices = draft.available_preference_targets.filter(
												(choice) => isExcluded(draft, choice)
											)}
											{@const allChoices = [
												...[...activeChoices].reverse(),
												...[...excludedChoices].reverse()
											]}
										<!-- All rungs: DoVi top, SDR bottom, excluded at very bottom -->
										{#each allChoices as choice (choice)}
											{@const excluded = isExcluded(draft, choice)}
											{@const zone = excluded ? null : zoneForChoice(draft, choice)}
											{@const isMeetBoundary = draft.meet_target === choice}
											{@const isExceedBoundary = draft.exceed_target === choice}
												<!-- svelte-ignore a11y_click_events_have_key_events -->
												<!-- svelte-ignore a11y_no_static_element_interactions -->
												<div
														class="rung"
														class:exceed={zone === 'exceed'}
														class:meet={zone === 'meet'}
														class:fail={zone === 'fail'}
														class:excluded={excluded}
														class:boundary={isMeetBoundary || isExceedBoundary}
													>
														<span class="rung-indicator"></span>
														<span class="rung-label">{PREFERENCE_LABEL[choice]}</span>
														{#if !excluded}
															{#if isMeetBoundary}
																<span class="rung-tag meet-tag">meet</span>
															{/if}
															{#if isExceedBoundary}
																<span class="rung-tag exceed-tag">exceed</span>
															{/if}
															{#if zone === 'fail'}
																<span class="rung-tag fails-tag">fails</span>
															{/if}
															<div class="rung-actions">
																<button
																	class="action-btn meet-btn"
																	class:active={isMeetBoundary}
																	type="button"
																	title="Set as meets target"
																	onclick={() => toggleMeetBoundary(draft, choice)}
																>✓</button>
																<button
																	class="action-btn exceed-btn"
																	class:active={isExceedBoundary}
																	type="button"
																	title="Set as exceeds target"
																	onclick={() => toggleExceedBoundary(draft, choice)}
																>★</button>
																<button
																	class="action-btn dismiss-btn"
																	type="button"
																	title="Mark as fails & exclude"
																	onclick={() => dismissRung(draft, choice)}
																>×</button>
															</div>
														{:else}
															<span class="rung-tag excluded-tag">excluded</span>
															<button
																class="rung-restore-btn"
																type="button"
																onclick={() => restoreRung(draft, choice)}
															>restore</button>
														{/if}
													</div>
											{/each}
											{/if}
									</div>
									<button
										class="collapse-ladder"
										type="button"
										onclick={() => collapseDraft(draft)}
									>
										Collapse
									</button>
								{:else}
									<button class="ladder-summary" type="button" onclick={() => expandDraft(draft)}>
										{#if draft.meet_target}
											<span class="summary-meet">
												Meet: <strong>{PREFERENCE_LABEL[draft.meet_target]}</strong>
											</span>
										{/if}
										{#if draft.exceed_target}
											{#if draft.meet_target}·{/if}
											<span class="summary-exceed">
												Exceed: <strong>{PREFERENCE_LABEL[draft.exceed_target]}</strong>
											</span>
										{/if}
										{#if !draft.meet_target && !draft.exceed_target}
											<span class="summary-meet">
												No targets set
											</span>
										{/if}
										{#if excludedCount(draft) > 0}
											·
											<span class="summary-excluded">
												Failed/Excluded: <strong>{excludedCount(draft)}</strong>
											</span>
										{/if}
										<span class="summary-edit">Edit</span>
									</button>
								{/if}
							</div>
						</div>
					{/each}
				</div>
			{:else}
				<p class="muted">
					No file-backed profile on this page currently exposes HDR preference targets.
				</p>
			{/if}
		</div>

		<div class="panel list">
			<div class="table-head">
				<h2>Movies</h2>
				<p>{data.data.total} results on this page slice.</p>
			</div>
			<div class="rows">
				<div class="row head">
					<span><a class="sort-link" href={sortHref('title')}>Title {sortGlyph('title')}</a></span>
					<span>HDR profiles</span>
					<span>Quality profile</span>
					<span
						><a class="sort-link" href={sortHref('preference_status')}
							>Status {sortGlyph('preference_status')}</a
						></span
					>
				</div>
				{#each data.data.items as item (item.id)}
					<a class="row item" href={`/hdr/${item.id}`}>
						<span class="title">
							<strong>{item.title}</strong>
							<small>{item.year} · {item.resolution ?? 'Unknown res'}</small>
						</span>
						<span class="tags-cell">
							<div class="stack-badges">
								<HdrBadge kinds={hdrOnlyKinds(item.hdr_tags)} kind={primaryHdrKind(item.hdr)} />
								{#if doviBadgeLabel(item)}
									<span
										class="dovi-badge"
										style={`--c:${doviBadgeTone(item)}`}
										title={doviBadgeTitle(item) ?? undefined}
									>
										{doviBadgeLabel(item)}
									</span>
								{/if}
							</div>
						</span>
						<span class="quality-profile">
							<strong>{item.profile_name ?? '—'}</strong>
							{#if item.profile_targets.length}
								<div class="quality-targets">
									<HdrBadge kinds={item.profile_targets} />
								</div>
							{:else}
								<small class="muted">No HDR targets</small>
							{/if}
						</span>
						<span class="status" style={`--tone:${STATUS_META[item.preference_status].tone}`}>
							<strong>{STATUS_META[item.preference_status].label}</strong>
							<small>{STATUS_META[item.preference_status].note}</small>
						</span>
					</a>
				{/each}
			</div>

			{#if totalPages > 1}
				<div class="pagination">
					<span class="pagination-info">
						Showing <strong>{startIndex}</strong> – <strong>{endIndex}</strong> of <strong>{totalItems}</strong> movies
					</span>
					<div class="pagination-buttons">
						<a
							class="page-btn"
							class:disabled={currentPage <= 1}
							href={currentPage > 1 ? withParams({ page: (currentPage - 1).toString() }) : undefined}
						>
							← Prev
						</a>
						{#each Array.from({ length: totalPages }, (_, i) => i + 1) as p}
							<a
								class="page-btn"
								class:active={p === currentPage}
								href={withParams({ page: p.toString() })}
							>
								{p}
							</a>
						{/each}
						<a
							class="page-btn"
							class:disabled={currentPage >= totalPages}
							href={currentPage < totalPages ? withParams({ page: (currentPage + 1).toString() }) : undefined}
						>
							Next →
						</a>
					</div>
				</div>
			{/if}
		</div>
	</section>
{/if}

<style>
	.page {
		display: flex;
		flex-direction: column;
		gap: 18px;
		padding: 22px 24px 32px;
	}
	.hero {
		display: flex;
		justify-content: space-between;
		gap: 18px;
		padding: 24px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background:
			radial-gradient(
				circle at top right,
				color-mix(in srgb, var(--dovi) 20%, transparent),
				transparent 35%
			),
			linear-gradient(160deg, var(--panel), var(--ink2));
	}
	.eyebrow {
		margin: 0 0 6px;
		font-size: 11px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--gold);
	}
	h1,
	h2,
	p {
		margin: 0;
	}
	h1 {
		font-size: 30px;
		letter-spacing: -0.02em;
	}
	.lede {
		margin-top: 10px;
		max-width: 72ch;
		color: var(--muted);
		line-height: 1.5;
	}
	.hero-side {
		display: flex;
		flex-direction: column;
		gap: 12px;
		align-items: stretch;
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(3, minmax(120px, 1fr));
		gap: 12px;
	}
	.analyze-btn {
		padding: 10px 16px;
		border: 1px solid var(--gold);
		border-radius: var(--radius);
		background: color-mix(in srgb, var(--gold) 14%, transparent);
		color: var(--gold);
		font-weight: 600;
		cursor: pointer;
	}
	.analyze-btn:hover:not(:disabled) {
		background: color-mix(in srgb, var(--gold) 24%, transparent);
	}
	.analyze-btn:disabled {
		opacity: 0.6;
		cursor: progress;
	}
	.analyze-banner {
		margin-top: 14px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.analyze-bar {
		height: 6px;
		border-radius: 999px;
		background: var(--line);
		overflow: hidden;
	}
	.analyze-bar span {
		display: block;
		height: 100%;
		background: var(--gold);
		transition: width 0.3s ease;
	}
	.stat,
	.panel {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.stat {
		padding: 14px 16px;
	}
	.stat strong {
		display: block;
		margin-top: 6px;
		font-size: 24px;
	}
	.label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
		color: var(--faint2);
	}
	.panel {
		padding: 16px;
	}
	.bar-head,
	.table-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		align-items: flex-start;
		margin-bottom: 14px;
	}
	.bar-head p,
	.table-head p {
		margin-top: 4px;
		color: var(--muted);
		line-height: 1.45;
	}
	.clear {
		color: var(--gold);
		font-size: 13px;
		white-space: nowrap;
	}
	.dist-bar {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.segment {
		flex: 1 1 calc(14% - 8px);
		min-width: 120px;
		padding: 12px 14px;
		border-radius: 10px;
		border: 1px solid color-mix(in srgb, var(--c, var(--faint)) 30%, var(--line));
		background:
			linear-gradient(
				90deg,
				color-mix(in srgb, var(--c, var(--faint)) 18%, transparent) var(--w),
				transparent var(--w)
			),
			var(--panel2);
		display: flex;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
		color: var(--muted);
	}
	.segment.active {
		border-color: color-mix(in srgb, var(--c, var(--faint)) 50%, var(--line));
		color: var(--text);
	}
	.segment strong {
		font-family: var(--font-mono);
		color: var(--c, var(--text));
	}
	.filters {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.filter-row {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 6px;
		font-size: 12px;
		color: var(--muted);
	}
	select,
	input[type='number'] {
		width: 100%;
		padding: 9px 10px;
		border-radius: 8px;
		border: 1px solid var(--line);
		background: var(--ink2);
		color: var(--text);
	}
	.tags {
		grid-template-columns: minmax(0, 1fr) auto auto;
		align-items: end;
	}
	.tag-group {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.group-label {
		font-size: 12px;
		color: var(--muted);
	}
	.checks {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 12px;
	}
	.check {
		flex-direction: row;
		align-items: center;
		gap: 8px;
		color: var(--text);
	}

	.apply,
	.save {
		height: 40px;
		padding: 0 16px;
		border: 1px solid color-mix(in srgb, var(--gold) 35%, var(--line));
		border-radius: 9px;
		background: var(--gold-soft);
		color: var(--gold);
		font-weight: 600;
	}
	.apply:disabled,
	.save:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.preference-panel {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.preference-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: 12px;
	}
	.pref-card {
		padding: 14px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--ink2);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.pref-head {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.pref-label {
		font-size: 11px;
		letter-spacing: 0.07em;
		text-transform: uppercase;
		color: var(--faint2);
	}
	.pref-controls {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	/* ── tier ladder ─────────────────────────────────── */
	.ladder {
		display: flex;
		flex-direction: column;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}
	.rung {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 9px 12px;
		cursor: pointer;
		border-bottom: 1px solid var(--line);
		transition: background 0.12s ease;
	}
	.rung:last-child {
		border-bottom: none;
	}
	.rung-indicator {
		width: 10px;
		height: 10px;
		border-radius: 50%;
		flex: none;
		background: var(--faint2);
		transition: background 0.12s ease;
	}
	.rung.meet .rung-indicator {
		background: var(--good);
		box-shadow: 0 0 6px color-mix(in srgb, var(--good) 50%, transparent);
	}
	.rung.exceed .rung-indicator {
		background: var(--gold);
		box-shadow: 0 0 6px color-mix(in srgb, var(--gold) 50%, transparent);
	}

	.rung.meet {
		background: color-mix(in srgb, var(--good) 10%, transparent);
	}
	.rung.meet:hover {
		background: color-mix(in srgb, var(--good) 18%, transparent);
	}
	.rung.exceed {
		background: color-mix(in srgb, var(--gold) 10%, transparent);
	}
	.rung.exceed:hover {
		background: color-mix(in srgb, var(--gold) 18%, transparent);
	}

	.rung.boundary {
		font-weight: 600;
	}
	.rung-label {
		font-size: 13px;
		flex: 1;
	}
	.rung-tag {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		font-weight: 700;
		padding: 2px 7px;
		border-radius: 4px;
	}
	.meet-tag {
		background: color-mix(in srgb, var(--good) 25%, transparent);
		color: var(--good);
	}
	.exceed-tag {
		background: color-mix(in srgb, var(--gold) 25%, transparent);
		color: var(--gold);
	}
	.fails-tag {
		background: color-mix(in srgb, var(--bad) 25%, transparent);
		color: var(--bad);
	}
	.collapse-ladder {
		font-size: 12px;
		color: var(--muted);
		background: transparent;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 6px 12px;
		cursor: pointer;
		align-self: flex-start;
	}
	.collapse-ladder:hover {
		background: var(--panel2);
		color: var(--text);
	}
	/* ── collapsed summary ──────────────────────────── */
	.ladder-summary {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--ink2);
		color: var(--text);
		cursor: pointer;
		text-align: left;
		font-size: 12px;
	}
	.ladder-summary:hover {
		background: var(--panel2);
	}
	.summary-meet strong {
		color: var(--good);
	}
	.summary-exceed strong {
		color: var(--gold);
	}
	.summary-edit {
		margin-left: auto;
		font-size: 11px;
		color: var(--muted);
	}
	.summary-excluded {
		font-size: 11px;
		color: var(--muted);
	}
	.summary-excluded strong {
		color: var(--bad);
	}
	/* ── rung actions & buttons ───────────────────── */
	.rung-actions {
		display: flex;
		align-items: center;
		gap: 6px;
		margin-left: auto;
		opacity: 0;
		transition: opacity 0.15s ease;
	}
	.rung:hover .rung-actions {
		opacity: 1;
	}
	.action-btn {
		width: 24px;
		height: 24px;
		border-radius: 50%;
		border: 1px solid var(--line);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		color: var(--faint2);
		cursor: pointer;
		font-size: 12px;
		line-height: 1;
		padding: 0;
		transition: all 0.15s ease;
	}
	.action-btn:hover {
		color: var(--text);
		border-color: var(--line2);
	}
	.action-btn.meet-btn:hover,
	.action-btn.meet-btn.active {
		color: var(--good);
		border-color: var(--good);
		background: color-mix(in srgb, var(--good) 15%, transparent);
	}
	.action-btn.exceed-btn:hover,
	.action-btn.exceed-btn.active {
		color: var(--gold);
		border-color: var(--gold);
		background: color-mix(in srgb, var(--gold) 15%, transparent);
	}
	.action-btn.dismiss-btn:hover {
		color: var(--bad);
		border-color: var(--bad);
		background: color-mix(in srgb, var(--bad) 15%, transparent);
	}
	/* ── dismissed / excluded rungs ──────────────── */
	.rung.excluded {
		background: color-mix(in srgb, var(--bad) 8%, transparent);
		color: var(--faint);
	}
	.rung.excluded:hover {
		background: color-mix(in srgb, var(--bad) 16%, transparent);
	}
	.rung.excluded .rung-indicator {
		background: var(--bad);
	}
	.excluded-tag {
		background: color-mix(in srgb, var(--bad) 25%, transparent);
		color: var(--bad);
	}
	.rung-restore-btn {
		font-size: 10px;
		color: var(--faint);
		background: transparent;
		border: 1px solid var(--line);
		border-radius: 4px;
		padding: 2px 8px;
		cursor: pointer;
		margin-left: auto;
		opacity: 0;
		transition: opacity 0.12s ease;
	}
	.rung.excluded:hover .rung-restore-btn {
		opacity: 1;
	}
	.rung-restore-btn:hover {
		color: var(--text);
		border-color: var(--line2);
	}
	/* ── fail rungs (red) ─────────────────── */
	.rung.fail {
		background: color-mix(in srgb, var(--bad) 8%, transparent);
		color: var(--muted);
	}
	.rung.fail:hover {
		background: color-mix(in srgb, var(--bad) 16%, transparent);
		color: var(--text);
	}
	.rung.fail .rung-indicator {
		background: var(--bad);
		box-shadow: 0 0 5px color-mix(in srgb, var(--bad) 40%, transparent);
	}

	.rows {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}
	.row {
		display: grid;
		grid-template-columns: 1.35fr 1.15fr 1.35fr 1.35fr;
		gap: 12px;
		padding: 12px 14px;
		align-items: center;
		border-bottom: 1px solid var(--line);
	}
	.row:last-child {
		border-bottom: none;
	}
	.row.head {
		background: var(--ink2);
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		font-weight: 700;
		color: var(--faint2);
	}
	.row.item:hover {
		background: var(--panel2);
	}
	.sort-link {
		color: inherit;
		text-decoration: none;
	}
	.title,
	.quality-profile,
	.status {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.title strong,
	.quality-profile strong,
	.status strong {
		font-size: 13px;
	}
	.title small,
	.status small {
		color: var(--muted);
		line-height: 1.35;
	}
	.tags-cell {
		display: flex;
		align-items: center;
	}
	.stack-badges {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 4px;
		align-items: center;
	}
	.dovi-badge {
		font-family: var(--font-mono);
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.02em;
		padding: 2px 6px;
		border-radius: 5px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 14%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 30%, transparent);
		white-space: nowrap;
	}
	.quality-profile {
		align-items: flex-start;
	}
	.quality-targets {
		display: inline-flex;
		flex-wrap: wrap;
		align-items: center;
	}
	.status strong {
		color: var(--tone);
	}
	.muted,
	.error {
		color: var(--muted);
	}
	.error {
		padding: 18px 20px;
		border-radius: var(--radius);
		border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line));
		background: color-mix(in srgb, var(--warn) 8%, transparent);
	}
	.pagination {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 14px 18px;
		border-top: 1px solid var(--line);
		background: var(--ink2);
		font-size: 13px;
		color: var(--muted);
	}
	.pagination-info strong {
		color: var(--text);
	}
	.pagination-buttons {
		display: flex;
		gap: 6px;
	}
	.page-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line);
		background: var(--panel);
		color: var(--text);
		text-decoration: none;
		font-size: 12px;
		font-weight: 500;
		transition: all 0.12s ease;
		cursor: pointer;
	}
	.page-btn:hover:not(.disabled):not(.active) {
		background: var(--panel2);
		border-color: var(--line2);
	}
	.page-btn.active {
		background: var(--text);
		border-color: var(--text);
		color: var(--ink);
		font-weight: 600;
		cursor: default;
	}
	.page-btn.disabled {
		opacity: 0.4;
		cursor: not-allowed;
		pointer-events: none;
	}
	@media (max-width: 1100px) {
		.hero,
		.bar-head,
		.table-head {
			flex-direction: column;
		}
		.stats,
		.filter-row {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.tags {
			grid-template-columns: 1fr;
		}
		.row {
			grid-template-columns: 1.2fr 1fr 1.2fr 1.2fr;
			font-size: 12px;
		}
	}
	@media (max-width: 760px) {
		.page {
			padding: 16px;
		}
		.stats,
		.filter-row {
			grid-template-columns: 1fr;
		}
		.row {
			grid-template-columns: 1fr;
		}
		.row.head {
			display: none;
		}
		.row.item {
			gap: 8px;
		}
	}
</style>
