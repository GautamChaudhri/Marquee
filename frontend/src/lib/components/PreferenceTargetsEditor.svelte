<script lang="ts">
	import HdrBadge from './HdrBadge.svelte';
	import { toast } from '$lib/toast';
	import type { HdrPreferenceChoice, RadarrOverlayProfilePreference } from '$lib/api/types';

	type PreferenceDraft = RadarrOverlayProfilePreference;
	type PreferenceUpdate = {
		profile_id: number;
		meet_target: HdrPreferenceChoice | null;
		exceed_target: HdrPreferenceChoice | null;
		excluded_targets: HdrPreferenceChoice[];
	};

	let {
		preferences,
		label,
		emptyMessage = 'No profiles with HDR targets — check your Radarr/Sonarr custom formats.',
		onSave
	}: {
		preferences: RadarrOverlayProfilePreference[];
		label: string;
		emptyMessage?: string;
		onSave: (profiles: PreferenceUpdate[]) => Promise<void>;
	} = $props();

	const PREFERENCE_LABEL: Record<HdrPreferenceChoice, string> = {
		sdr: 'SDR',
		hdr: 'HDR',
		hdr10: 'HDR10',
		hdr10p: 'HDR10+',
		dovi_no_fallback: 'DoVi w/o fallback',
		dovi_fallback: 'DoVi'
	};
	const PREFERENCE_RANK: Record<HdrPreferenceChoice, number> = {
		sdr: -1,
		hdr: 0,
		hdr10: 1,
		hdr10p: 2,
		dovi_no_fallback: 3,
		dovi_fallback: 4
	};

	let drafts = $state<PreferenceDraft[]>([]);
	let saving = $state(false);

	// Tier ladder state per draft
	let expandedDrafts = $state<Record<number, boolean>>({});
	// Excluded tiers keyed by "profile_id:choice" for deep reactivity
	let excludedKeys = $state<Record<string, boolean>>({});

	$effect(() => {
		drafts = preferences.map((preference) => ({ ...preference }));

		const nextKeys: Record<string, boolean> = {};
		for (const pref of preferences) {
			for (const choice of pref.excluded_targets ?? []) {
				nextKeys[excludedKey(pref, choice)] = true;
			}
		}
		excludedKeys = nextKeys;
	});

	function excludedKey(draft: PreferenceDraft, choice: string): string {
		return `${draft.profile_id}:${choice}`;
	}

	function isExcluded(draft: PreferenceDraft, choice: string): boolean {
		return !!excludedKeys[excludedKey(draft, choice)];
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
			if (draft.exceed_target && PREFERENCE_RANK[draft.exceed_target] <= PREFERENCE_RANK[choice]) {
				draft.exceed_target = null;
			}
		}
	}

	function toggleExceedBoundary(draft: PreferenceDraft, choice: HdrPreferenceChoice): void {
		if (draft.exceed_target === choice) {
			draft.exceed_target = null;
		} else {
			draft.exceed_target = choice;
			if (draft.meet_target && PREFERENCE_RANK[draft.meet_target] >= PREFERENCE_RANK[choice]) {
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
		if (!drafts.length) return;
		saving = true;
		try {
			await onSave(
				drafts.map((draft) => ({
					profile_id: draft.profile_id,
					meet_target: draft.meet_target,
					exceed_target: draft.exceed_target,
					excluded_targets: draft.available_preference_targets.filter((choice) =>
						isExcluded(draft, choice)
					)
				}))
			);
			toast(`${label} preference targets saved`, 'good');
		} catch (e) {
			toast(e instanceof Error ? e.message : `Could not save ${label} preference targets`, 'bad');
		} finally {
			saving = false;
		}
	}
</script>

<div class="panel preference-panel">
	<div class="table-head">
		<div>
			<h2>{label}</h2>
			<p>
				Each profile defines what counts as <strong>meet</strong> and optionally
				<strong>exceed</strong>. Below target is derived automatically.
			</p>
		</div>
		<button class="save" type="button" onclick={savePreferences} disabled={saving}>
			{saving ? 'Saving…' : 'Save preferences'}
		</button>
	</div>
	{#if drafts.length}
		<div class="preference-grid">
			{#each drafts as draft (draft.profile_id)}
				<div class="pref-card">
					<div class="pref-head">
						<strong>{draft.profile_name}</strong>
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
									{@const excludedChoices = draft.available_preference_targets.filter((choice) =>
										isExcluded(draft, choice)
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
										<div
											class="rung"
											class:exceed={zone === 'exceed'}
											class:meet={zone === 'meet'}
											class:fail={zone === 'fail'}
											class:excluded
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
														onclick={() => toggleMeetBoundary(draft, choice)}>✓</button
													>
													<button
														class="action-btn exceed-btn"
														class:active={isExceedBoundary}
														type="button"
														title="Set as exceeds target"
														onclick={() => toggleExceedBoundary(draft, choice)}>★</button
													>
													<button
														class="action-btn dismiss-btn"
														type="button"
														title="Mark as fails & exclude"
														onclick={() => dismissRung(draft, choice)}>×</button
													>
												</div>
											{:else}
												<span class="rung-tag excluded-tag">excluded</span>
												<button
													class="rung-restore-btn"
													type="button"
													onclick={() => restoreRung(draft, choice)}>restore</button
												>
											{/if}
										</div>
									{/each}
								{/if}
							</div>
							<button class="collapse-ladder" type="button" onclick={() => collapseDraft(draft)}>
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
									<span class="summary-meet"> No targets set </span>
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
		<p class="muted">{emptyMessage}</p>
	{/if}
</div>

<style>
	h2 {
		margin: 0;
		font-size: 16px;
	}
	p {
		margin: 0;
	}
	.preference-panel {
		display: flex;
		flex-direction: column;
		gap: 14px;
		padding: 16px;
		border: 1px solid var(--line);
		border-radius: var(--radius);
		background: var(--panel);
	}
	.table-head {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		align-items: flex-start;
	}
	.table-head p {
		margin-top: 4px;
		color: var(--muted);
		line-height: 1.45;
	}
	.save {
		height: 40px;
		padding: 0 16px;
		border: 1px solid color-mix(in srgb, var(--gold) 35%, var(--line));
		border-radius: 9px;
		background: var(--gold-soft);
		color: var(--gold);
		font-weight: 600;
		white-space: nowrap;
	}
	.save:disabled {
		opacity: 0.6;
		cursor: default;
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
	.muted {
		color: var(--muted);
	}
</style>
