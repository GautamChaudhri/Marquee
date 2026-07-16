<script lang="ts">
	import { SvelteURLSearchParams, SvelteSet } from 'svelte/reactivity';
	import { page } from '$app/state';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import HdrBadge from '$lib/components/HdrBadge.svelte';
	import SegmentedBar from '$lib/components/SegmentedBar.svelte';
	import UniformityChip from '$lib/components/UniformityChip.svelte';
	import { SHOW_STATUS_META, UNIFORMITY_META, HDR_TAG_LABEL } from '$lib/hdr-display';
	import type {
		HdrKind,
		ShowStatus,
		ShowUniformity,
		HdrTvListItem,
		ShowRollup
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let currentPage = $derived(data.data?.page ?? 1);
	let pageSize = $derived(data.data?.page_size ?? 100);
	let totalItems = $derived(data.data?.total ?? 0);
	let totalPages = $derived(Math.ceil(totalItems / pageSize));
	let startIndex = $derived(totalItems > 0 ? (currentPage - 1) * pageSize + 1 : 0);
	let endIndex = $derived(Math.min(currentPage * pageSize, totalItems));

	const TAGS: HdrKind[] = ['sdr', 'hdr', 'hdr10', 'hdr10p', 'dovi', 'dovi_no_fallback'];

	const DIST_COLORS: Record<string, string> = {
		sdr: 'var(--faint)',
		hdr: 'var(--good)',
		hdr10: 'var(--info)',
		hdr10p: 'var(--dovi)',
		dovi: 'var(--gold)',
		dovi_no_fallback: 'var(--bad)'
	};

	const showStatuses = Object.keys(SHOW_STATUS_META) as ShowStatus[];
	const uniformities = Object.keys(UNIFORMITY_META) as ShowUniformity[];

	function selectedTags(): string[] {
		const raw = page.url.searchParams.getAll('hdr_tags');
		if (!raw.length) return [];
		return raw
			.flatMap((value) => value.split(','))
			.map((value) => value.trim())
			.filter(Boolean);
	}

	function withParams(patch: Record<string, string | null>, tagsOverride?: string[]): string {
		const sp = new SvelteURLSearchParams(page.url.searchParams);
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
		return query ? `/hdr/tv?${query}` : '/hdr/tv';
	}

	function toggleTagHref(tag: string): string {
		const current = new SvelteSet(selectedTags());
		if (current.has(tag)) current.delete(tag);
		else current.add(tag);
		return withParams({}, [...current]);
	}

	function clearHref(): string {
		return '/hdr/tv';
	}

	function pct(count: number, total: number): string {
		if (!total || count <= 0) return '0%';
		return `${Math.max(4, (count / total) * 100)}%`;
	}

	function sortHref(sortBy: 'title' | 'status' | 'coverage'): string {
		const currentSort = (page.url.searchParams.get('sort_by') as typeof sortBy | null) ?? 'title';
		const currentDir = (page.url.searchParams.get('sort_dir') as 'asc' | 'desc' | null) ?? 'asc';
		const nextDir = currentSort === sortBy ? (currentDir === 'asc' ? 'desc' : 'asc') : 'asc';
		return withParams({ sort_by: sortBy, sort_dir: nextDir });
	}

	function sortGlyph(sortBy: 'title' | 'status' | 'coverage'): string {
		const currentSort = page.url.searchParams.get('sort_by') ?? 'title';
		if (currentSort !== sortBy) return '';
		return (page.url.searchParams.get('sort_dir') ?? 'asc') === 'asc' ? '↑' : '↓';
	}

	function getUnionKinds(item: HdrTvListItem) {
		return item.rollup.uniformity === 'uniform'
			? (item.rollup.uniform_tags ?? [])
			: item.rollup.union_tags;
	}

	function segmentsForRollup(rollup: ShowRollup) {
		return [
			{ key: 'exceeds', count: rollup.status_counts.exceeds_target ?? 0, tone: 'gold' as const },
			{ key: 'meets', count: rollup.status_counts.meets_target ?? 0, tone: 'good' as const },
			{ key: 'below', count: rollup.status_counts.below_target ?? 0, tone: 'warn' as const },
			{ key: 'unknown', count: rollup.episodes_unknown ?? 0, tone: 'low' as const },
			{ key: 'no_target', count: rollup.status_counts.no_hdr_target ?? 0, tone: 'muted' as const }
		];
	}
</script>

{#if data.error || !data.data}
	<section class="page error-page">
		<SectionHeader title="TV HDR Management" subtitle="Television series HDR rollup summary." />
		<div class="error">{data.error ?? 'Failed to load Television HDR.'}</div>
	</section>
{:else}
	<section class="page">
		<div class="hero">
			<div>
				<p class="eyebrow">Toolbox</p>
				<h1>TV HDR Management</h1>
				<p class="lede">
					A consolidated rollup of TV HDR truth, uniformity indicators, and profile target
					compliance.
				</p>
			</div>
			<div class="hero-side">
				<div class="stats">
					<div class="stat">
						<span class="label">Shows</span>
						<strong>{data.data.total}</strong>
					</div>
					<div class="stat">
						<span class="label">Profiles</span>
						<strong>{data.data.profile_preferences.length}</strong>
					</div>
					<div class="stat">
						<span class="label">Episodes</span>
						<strong
							>{data.data.distribution.hdr +
								data.data.distribution.sdr +
								(data.data.distribution.dovi_no_fallback ?? 0)}</strong
						>
					</div>
				</div>
			</div>
		</div>

		<div class="panel">
			<div class="bar-head">
				<div>
					<h2>Episode HDR distribution</h2>
					<p>
						Total episode counts across all downloaded seasons. Hover over a segment for the count.
					</p>
				</div>
				<a class="clear" href={clearHref()}>Clear filters</a>
			</div>
			<div class="dist-bar">
				{#each data.data.distribution_order as key (key)}
					{@const count = data.data.distribution[key as keyof typeof data.data.distribution] ?? 0}
					<a
						class="segment"
						class:active={selectedTags().includes(key)}
						href={toggleTagHref(key)}
						style={`--w:${pct(count, Math.max(data.data.total * 10, 1))};--c:${DIST_COLORS[key] ?? 'var(--faint)'}`}
					>
						<span>{HDR_TAG_LABEL[key] ?? key}</span>
						<strong>{count}</strong>
					</a>
				{/each}
			</div>
		</div>

		<form class="panel filters" method="GET">
			<input type="hidden" name="sort_by" value={data.query.sort_by ?? 'title'} />
			<input type="hidden" name="sort_dir" value={data.query.sort_dir ?? 'asc'} />
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
						{#each showStatuses as status (status)}
							<option value={status} selected={data.query.preference_status === status}>
								{SHOW_STATUS_META[status].label}
							</option>
						{/each}
					</select>
				</label>
				<label>
					<span>Uniformity</span>
					<select name="uniformity">
						<option value="">Any uniformity</option>
						{#each uniformities as uniformity (uniformity)}
							<option value={uniformity} selected={data.query.uniformity === uniformity}>
								{UNIFORMITY_META[uniformity].label}
							</option>
						{/each}
					</select>
				</label>
				<label class="check-wrap">
					<input
						type="checkbox"
						name="dovi_no_fallback"
						value="true"
						checked={data.query.dovi_no_fallback === true}
					/>
					<span>DoVi w/o fallback</span>
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
								<span>{HDR_TAG_LABEL[tag]}</span>
							</label>
						{/each}
					</div>
				</div>
				<button class="apply" type="submit">Apply filters</button>
			</div>
		</form>

		<div class="panel list">
			<div class="table-head">
				<h2>Shows</h2>
				<p>{data.data.total} results.</p>
			</div>
			<div class="rows">
				<div class="row head">
					<span><a class="sort-link" href={sortHref('title')}>Title {sortGlyph('title')}</a></span>
					<span>HDR profiles</span>
					<span>Quality profile</span>
					<span
						><a class="sort-link" href={sortHref('status')}
							>Uniformity & Status {sortGlyph('status')}</a
						></span
					>
					<span
						><a class="sort-link" href={sortHref('coverage')}>Coverage {sortGlyph('coverage')}</a
						></span
					>
				</div>
				{#each data.data.items as item (item.id)}
					<a class="row item" href={`/hdr/tv/${item.id}`}>
						<span class="title">
							<strong>{item.title}</strong>
							<small
								>{item.year ?? '—'} · {item.seasons_count}
								{item.seasons_count === 1 ? 'season' : 'seasons'}</small
							>
						</span>
						<span class="tags-cell">
							<div class="stack-badges">
								<HdrBadge kinds={getUnionKinds(item)} />
								{#if item.rollup.uniformity !== 'uniform'}
									<span class="mixed-badge">Mixed</span>
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
						<span class="uniformity-col">
							<UniformityChip uniformity={item.rollup.uniformity} />
							<span
								class="status-meta"
								style={`color:${SHOW_STATUS_META[item.rollup.status].tone === 'gold' ? 'var(--gold)' : SHOW_STATUS_META[item.rollup.status].tone === 'good' ? 'var(--good)' : SHOW_STATUS_META[item.rollup.status].tone === 'warn' ? 'var(--warn)' : SHOW_STATUS_META[item.rollup.status].tone === 'bad' ? 'var(--bad)' : 'var(--muted)'}`}
							>
								{SHOW_STATUS_META[item.rollup.status].label}
							</span>
						</span>
						<span class="coverage-col">
							<SegmentedBar
								segments={segmentsForRollup(item.rollup)}
								total={item.rollup.episodes_total}
								fractionText={`${item.rollup.meeting_fraction.met}/${item.rollup.meeting_fraction.of} meet`}
							/>
						</span>
					</a>
				{/each}
			</div>

			{#if totalPages > 1}
				<div class="pagination">
					<span class="pagination-info">
						Showing <strong>{startIndex}</strong> – <strong>{endIndex}</strong> of
						<strong>{totalItems}</strong> shows
					</span>
					<div class="pagination-buttons">
						<a
							class="page-btn"
							class:disabled={currentPage <= 1}
							href={currentPage > 1
								? withParams({ page: (currentPage - 1).toString() })
								: undefined}
						>
							← Prev
						</a>
						{#each Array.from({ length: totalPages }, (_, i) => i + 1) as p (p)}
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
							href={currentPage < totalPages
								? withParams({ page: (currentPage + 1).toString() })
								: undefined}
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
		align-items: center;
		flex-shrink: 0;
	}
	.stats {
		display: flex;
		gap: 24px;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.stat .label {
		font-size: 11px;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.stat strong {
		font-size: 24px;
		font-family: var(--font-mono);
		font-weight: 500;
	}
	.panel {
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 16px;
	}
	.bar-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		margin-bottom: 12px;
	}
	.bar-head h2 {
		font-size: 14px;
	}
	.bar-head p {
		margin-top: 4px;
		font-size: 12px;
		color: var(--muted);
	}
	.clear {
		font-size: 12px;
		color: var(--gold);
		text-decoration: none;
	}
	.clear:hover {
		text-decoration: underline;
	}
	.dist-bar {
		display: flex;
		height: 24px;
		border-radius: 6px;
		overflow: hidden;
		background: var(--ink3);
		border: 1px solid var(--line);
	}
	.segment {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0 10px;
		font-size: 11px;
		color: var(--text);
		text-decoration: none;
		background: var(--c);
		width: var(--w);
		min-width: fit-content;
		border-right: 1px solid var(--line);
		box-sizing: border-box;
		opacity: 0.85;
		transition: opacity 0.12s ease;
	}
	.segment:last-child {
		border-right: none;
	}
	.segment:hover {
		opacity: 1;
	}
	.segment.active {
		opacity: 1;
		font-weight: 700;
		outline: 1.5px solid var(--text);
		outline-offset: -1.5px;
	}
	.filters {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.filter-row {
		display: flex;
		flex-wrap: wrap;
		gap: 16px;
		align-items: flex-end;
	}
	.filter-row label {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.filter-row label span {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
	}
	.filter-row select {
		height: 38px;
		min-width: 160px;
		padding: 0 12px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--ink2);
		color: var(--text);
		outline: none;
	}
	.check-wrap {
		display: flex;
		flex-direction: row !important;
		align-items: center;
		gap: 8px !important;
		height: 38px;
		cursor: pointer;
	}
	.check-wrap input {
		cursor: pointer;
	}
	.tag-group {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.group-label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
	}
	.checks {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		padding: 8px 12px;
		background: var(--ink2);
		border: 1px solid var(--line);
		border-radius: 6px;
		height: 38px;
		align-items: center;
		box-sizing: border-box;
	}
	.check {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		cursor: pointer;
	}
	.check input {
		margin: 0;
		cursor: pointer;
	}
	.apply {
		height: 38px;
		padding: 0 20px;
		border: 1px solid color-mix(in srgb, var(--gold) 35%, var(--line));
		border-radius: 6px;
		background: var(--gold-soft);
		color: var(--gold);
		font-weight: 600;
		cursor: pointer;
		white-space: nowrap;
	}
	.apply:hover {
		background: color-mix(in srgb, var(--gold) 20%, var(--gold-soft));
	}
	.table-head {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		margin-bottom: 14px;
		border-bottom: 1px solid var(--line);
		padding-bottom: 10px;
	}
	.table-head h2 {
		font-size: 16px;
	}
	.table-head p {
		font-size: 12px;
		color: var(--muted);
	}
	.rows {
		display: flex;
		flex-direction: column;
	}
	.row {
		display: grid;
		grid-template-columns: 2.2fr 1.6fr 1.6fr 1.6fr 2.5fr;
		gap: 12px;
		padding: 10px 12px;
		align-items: center;
		text-decoration: none;
		color: inherit;
		border-bottom: 1px solid var(--line);
		border-radius: var(--radius-sm);
	}
	.row.head {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--muted);
		font-weight: 600;
		border-bottom: 2px solid var(--line);
	}
	.sort-link {
		color: inherit;
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		gap: 4px;
	}
	.sort-link:hover {
		color: var(--text);
	}
	.row.item {
		transition: background 0.12s ease;
	}
	.row.item:hover {
		background: var(--ink3);
	}
	.title {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.title strong {
		font-size: 13.5px;
		color: var(--text);
	}
	.title small {
		font-size: 11.5px;
		color: var(--muted);
	}
	.stack-badges {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px;
	}
	.mixed-badge {
		font-size: 10px;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 5px;
		color: var(--muted);
		background: var(--ink3);
		border: 1px dashed var(--line);
	}
	.quality-profile {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.quality-profile strong {
		font-size: 13px;
	}
	.quality-targets {
		display: flex;
		gap: 4px;
	}
	.uniformity-col {
		display: flex;
		flex-direction: column;
		gap: 4px;
		align-items: flex-start;
	}
	.status-meta {
		font-size: 11px;
		font-weight: 500;
	}
	.pagination {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-top: 16px;
		padding-top: 14px;
		border-top: 1px solid var(--line);
	}
	.pagination-info {
		font-size: 12px;
		color: var(--muted);
	}
	.pagination-buttons {
		display: flex;
		gap: 6px;
	}
	.page-btn {
		padding: 6px 12px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--ink2);
		color: var(--text);
		text-decoration: none;
		font-size: 12px;
		transition: all 0.12s ease;
	}
	.page-btn:hover:not(.disabled) {
		background: var(--ink3);
		border-color: var(--faint);
	}
	.page-btn.active {
		background: var(--gold-soft);
		color: var(--gold);
		border-color: var(--gold);
		font-weight: 600;
	}
	.page-btn.disabled {
		opacity: 0.4;
		pointer-events: none;
		cursor: default;
	}
	.error {
		padding: 18px 20px;
		border-radius: var(--radius);
		border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line));
		background: color-mix(in srgb, var(--warn) 8%, transparent);
		color: var(--muted);
	}
</style>
