<script lang="ts">
	import { page } from '$app/state';
	import HdrBadge from '$lib/components/HdrBadge.svelte';
	import type {
		HdrKind,
		HdrTargetStatus,
		RadarrOverlayItem
	} from '$lib/api/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const TAGS: (HdrKind | 'unknown')[] = [
		'hdr',
		'hdr10',
		'hdr10p',
		'dovi',
		'dovi_no_fallback',
		'sdr',
		'unknown'
	];
	const STATUS_META: Record<
		HdrTargetStatus,
		{ label: string; tone: string; note: string }
	> = {
		met_target: { label: 'Met target', tone: 'var(--good)', note: 'Current file satisfies the profile HDR target.' },
		below_target: { label: 'Below target', tone: 'var(--warn)', note: 'Current file is missing one or more targeted HDR formats.' },
		no_hdr_target: { label: 'No HDR target', tone: 'var(--faint)', note: 'This quality profile does not actively seek HDR formats.' },
		no_file: { label: 'No file', tone: 'var(--info)', note: 'Movie is monitored but there is no active file to inspect.' }
	};
	const TAG_LABEL: Record<string, string> = {
		hdr: 'HDR',
		hdr10: 'HDR10',
		hdr10p: 'HDR10+',
		dovi: 'DoVi',
		dovi_no_fallback: 'DoVi no fallback',
		sdr: 'SDR',
		unknown: 'Unknown'
	};

	function selectedTags(): string[] {
		const raw = page.url.searchParams.getAll('hdr_tags');
		if (!raw.length) return [];
		return raw
			.flatMap((value) => value.split(','))
			.map((value) => value.trim())
			.filter(Boolean);
	}

	function withParams(
		patch: Record<string, string | null>,
		tagsOverride?: string[]
	): string {
		const sp = new URLSearchParams(page.url.searchParams);
		if (tagsOverride) {
			sp.delete('hdr_tags');
			for (const tag of tagsOverride) sp.append('hdr_tags', tag);
		}
		for (const [key, value] of Object.entries(patch)) {
			if (value === null || value === '') sp.delete(key);
			else sp.set(key, value);
		}
		sp.delete('page');
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

	function cutoffLabel(item: RadarrOverlayItem): string {
		if (item.cf_cutoff == null) return '—';
		const suffix = item.cutoff_met == null ? '' : item.cutoff_met ? ' met' : ' open';
		return `${item.cf_cutoff}${suffix}`;
	}
</script>

{#if data.error || !data.data}
	<section class="page">
		<div class="hero">
			<div>
				<p class="eyebrow">Toolbox</p>
				<h1>Radarr Overlay</h1>
				<p class="lede">Read-only Radarr metadata coverage for HDR, quality profiles, and custom-format scoring.</p>
			</div>
		</div>
		<div class="error">{data.error ?? 'Failed to load Radarr overlay.'}</div>
	</section>
{:else}
	<section class="page">
		<div class="hero">
			<div>
				<p class="eyebrow">Toolbox</p>
				<h1>Radarr Overlay</h1>
				<p class="lede">
					A consolidated readout of HDR truth, custom-format score posture, and per-profile target compliance.
				</p>
			</div>
			<div class="stats">
				<div class="stat">
					<span class="label">Movies</span>
					<strong>{data.data.total}</strong>
				</div>
				<div class="stat">
					<span class="label">Profiles</span>
					<strong>{data.data.profiles.length}</strong>
				</div>
				<div class="stat">
					<span class="label">DoVi no fallback</span>
					<strong>{data.data.distribution.dovi_no_fallback}</strong>
				</div>
			</div>
		</div>

		<div class="panel">
			<div class="bar-head">
				<div>
					<h2>HDR distribution</h2>
					<p>Each movie contributes to every HDR tag it actually carries, so hybrid files like <code>DV HDR10</code> count in both groups.</p>
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
						style={`--w:${pct(count, Math.max(data.data.total, 1))}`}
					>
						<span>{TAG_LABEL[key] ?? key}</span>
						<strong>{count}</strong>
					</a>
				{/each}
			</div>
		</div>

		<form class="panel filters" method="GET">
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
					<span>Target status</span>
					<select name="hdr_target_status">
						<option value="">Any status</option>
						{#each Object.entries(STATUS_META) as [value, meta] (value)}
							<option value={value} selected={data.query.hdr_target_status === value}>{meta.label}</option>
						{/each}
					</select>
				</label>
				<label>
					<span>Min CF score</span>
					<input name="cf_score_min" type="number" min="0" value={data.query.cf_score_min ?? ''} />
				</label>
				<label>
					<span>Max CF score</span>
					<input name="cf_score_max" type="number" min="0" value={data.query.cf_score_max ?? ''} />
				</label>
				<label>
					<span>Sort</span>
					<select name="sort_by">
						<option value="cf_score" selected={data.query.sort_by === 'cf_score'}>CF score</option>
						<option value="hdr_target_status" selected={data.query.sort_by === 'hdr_target_status'}>Target status</option>
						<option value="title" selected={data.query.sort_by === 'title'}>Title</option>
						<option value="year" selected={data.query.sort_by === 'year'}>Year</option>
					</select>
				</label>
				<label>
					<span>Direction</span>
					<select name="sort_dir">
						<option value="desc" selected={data.query.sort_dir === 'desc'}>Desc</option>
						<option value="asc" selected={data.query.sort_dir === 'asc'}>Asc</option>
					</select>
				</label>
			</div>

			<div class="filter-row tags">
				<div class="tag-group">
					<span class="group-label">HDR tags</span>
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
				<label class="check single">
					<input
						type="checkbox"
						name="dovi_no_fallback"
						value="true"
						checked={data.query.dovi_no_fallback === true}
					/>
					<span>Only DoVi without fallback</span>
				</label>
				<button class="apply" type="submit">Apply filters</button>
			</div>
		</form>

		<div class="panel list">
			<div class="table-head">
				<h2>Movies</h2>
				<p>{data.data.total} results on this page slice.</p>
			</div>
			<div class="rows">
				<div class="row head">
					<span>Title</span>
					<span>HDR tags</span>
					<span>Profile</span>
					<span>CF score</span>
					<span>Cutoff</span>
					<span>Targets</span>
					<span>Status</span>
				</div>
				{#each data.data.items as item (item.id)}
					<a class="row item" href={`/films/${item.id}`}>
						<span class="title">
							<strong>{item.title}</strong>
							<small>{item.year} · {item.resolution ?? 'Unknown res'}</small>
						</span>
						<span class="tags-cell">
							<HdrBadge kinds={item.hdr_tags} kind={item.hdr} />
						</span>
						<span class="profile">
							<strong>{item.profile_name ?? '—'}</strong>
							<small>{item.profile_id ? `#${item.profile_id}` : 'No profile'}</small>
						</span>
						<span class="mono">{item.cf_score}</span>
						<span class="mono">{cutoffLabel(item)}</span>
						<span class="tags-cell">
							{#if item.hdr_targets.length}
								<HdrBadge kinds={item.hdr_targets} />
							{:else}
								<span class="muted">—</span>
							{/if}
						</span>
						<span class="status" style={`--tone:${STATUS_META[item.hdr_target_status].tone}`}>
							<strong>{STATUS_META[item.hdr_target_status].label}</strong>
							<small>{STATUS_META[item.hdr_target_status].note}</small>
						</span>
					</a>
				{/each}
			</div>
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
			radial-gradient(circle at top right, color-mix(in srgb, var(--dovi) 20%, transparent), transparent 35%),
			linear-gradient(160deg, var(--panel), var(--ink2));
	}
	.eyebrow {
		margin: 0 0 6px;
		font-size: 11px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--gold);
	}
	h1, h2, p {
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
	.stats {
		display: grid;
		grid-template-columns: repeat(3, minmax(120px, 1fr));
		gap: 12px;
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
		border: 1px solid var(--line);
		background:
			linear-gradient(90deg, color-mix(in srgb, var(--gold-soft) 30%, transparent) var(--w), transparent var(--w)),
			var(--panel2);
		display: flex;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
		color: var(--muted);
	}
	.segment.active {
		border-color: color-mix(in srgb, var(--gold) 40%, var(--line));
		color: var(--text);
	}
	.segment strong {
		font-family: var(--font-mono);
		color: var(--text);
	}
	.filters {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.filter-row {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
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
	.check.single {
		align-self: center;
	}
	.apply {
		height: 40px;
		padding: 0 16px;
		border: 1px solid color-mix(in srgb, var(--gold) 35%, var(--line));
		border-radius: 9px;
		background: var(--gold-soft);
		color: var(--gold);
		font-weight: 600;
	}
	.rows {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}
	.row {
		display: grid;
		grid-template-columns: 1.35fr 1.1fr 1fr 90px 90px 1.1fr 1.35fr;
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
	.title,
	.profile,
	.status {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.title strong,
	.profile strong,
	.status strong {
		font-size: 13px;
	}
	.title small,
	.profile small,
	.status small {
		color: var(--muted);
		line-height: 1.35;
	}
	.mono {
		font-family: var(--font-mono);
		font-size: 13px;
		color: var(--text);
	}
	.tags-cell {
		display: flex;
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
			grid-template-columns: 1.3fr 1fr 1fr 80px 80px 1fr 1.2fr;
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
