<script lang="ts">
	import type { SubtitleTrack, ContainerCapabilities, SubtitlePlan } from '$lib/api/types';
	import TrackRow from './TrackRow.svelte';
	import PlanReview from './PlanReview.svelte';
	import ConfirmDialog from '../ConfirmDialog.svelte';
	import ProgressBar from '../ProgressBar.svelte';
		import { createPlan, extractTrack, getInventory } from '$lib/api/subtitles';
	import { confirmJob, cancelJob } from '$lib/api/media-jobs';
	import { subscribe } from '$lib/sse';
	import { toast } from '$lib/toast';

	let {
		tracks,
		mediaFileId,
		movieId,
		capabilities,
		onMutationComplete
	}: {
		tracks: SubtitleTrack[];
		mediaFileId: number;
		movieId: number;
		capabilities: ContainerCapabilities;
		onMutationComplete: () => void;
	} = $props();

	// Selection state
	let selectedIds = $state(new Set<string>());
	let lastSelectedIndex = $state<number | null>(null);

	// Whitelist/blacklist state
	let whitelistText = $state('');
	let blacklistText = $state('');

	// Action panels
	let selectedOp = $state<'remove' | 'embed' | 'metadata' | 'extract' | null>(null);

	// Metadata edits fields state
	let metaTitle = $state('');
	let metaLang = $state('');
	let metaDefault = $state<'true' | 'false' | 'keep'>('keep');
	let metaForced = $state<'true' | 'false' | 'keep'>('keep');
	let metaSdh = $state<'true' | 'false' | 'keep'>('keep');
	let metaCommentary = $state<'true' | 'false' | 'keep'>('keep');

	// Extract options
	let deleteOriginalAfterExtract = $state(true);

	// Plan / execution state
	let submitting = $state(false);
	let activeJobId = $state<string | null>(null);
	let planResult = $state<SubtitlePlan | null>(null);
	let progressPercent = $state(0);
	let progressStage = $state('');
	let progressMessage = $state('');

	// Select helpers
	let selectedTracks = $derived(tracks.filter(t => selectedIds.has(t.id)));
	let allSelected = $derived(tracks.length > 0 && selectedIds.size === tracks.length);

	function toggleSelectAll() {
		if (allSelected) {
			selectedIds.clear();
		} else {
			tracks.forEach(t => selectedIds.add(t.id));
		}
		lastSelectedIndex = null;
	}

	function selectSource(source: 'embedded' | 'external') {
		tracks.forEach(t => {
			if (t.source === source) selectedIds.add(t.id);
		});
	}

	function selectFlag(flag: 'is_forced' | 'is_sdh' | 'is_commentary') {
		tracks.forEach(t => {
			if (t[flag]) selectedIds.add(t.id);
		});
	}

	function applyWhitelist() {
		const codes = whitelistText.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
		if (codes.length === 0) return;
		tracks.forEach(t => {
			if (codes.includes(t.language_tag.toLowerCase()) || codes.includes((t.language_raw || '').toLowerCase())) {
				selectedIds.add(t.id);
			}
		});
		whitelistText = '';
	}

	function applyBlacklist() {
		const codes = blacklistText.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
		if (codes.length === 0) return;
		tracks.forEach(t => {
			if (codes.includes(t.language_tag.toLowerCase()) || codes.includes((t.language_raw || '').toLowerCase())) {
				selectedIds.delete(t.id);
			}
		});
		blacklistText = '';
	}

	function onRowToggle(index: number, e: MouseEvent) {
		const track = tracks[index];
		if (e.shiftKey && lastSelectedIndex !== null) {
			const start = Math.min(lastSelectedIndex, index);
			const end = Math.max(lastSelectedIndex, index);
			const checkState = !selectedIds.has(track.id);
			for (let i = start; i <= end; i++) {
				if (checkState) {
					selectedIds.add(tracks[i].id);
				} else {
					selectedIds.delete(tracks[i].id);
				}
			}
		} else {
			if (selectedIds.has(track.id)) {
				selectedIds.delete(track.id);
			} else {
				selectedIds.add(track.id);
			}
			lastSelectedIndex = index;
		}
	}

	function onRowSelect(index: number) {
		const track = tracks[index];
		// If this track is already the only selected one, deselect it
		if (selectedIds.size === 1 && selectedIds.has(track.id)) {
			selectedIds.clear();
			lastSelectedIndex = null;
		} else {
			selectedIds.clear();
			selectedIds.add(track.id);
			lastSelectedIndex = index;
		}
	}

		// Subscribes to and monitors a backend job until terminal state.
		// Listens for generic SSE ``message`` events (the default for unnamed
		// ``data:`` lines) plus the terminal ``done`` event.  The backend emits
		// ``{stage, state, message, progress}`` on every state change and
		// ``event: done`` with status when the job finishes.
		function monitorJob(jobId: string, onDone: () => Promise<void> | void): Promise<void> {
			return new Promise((resolve, reject) => {
				const unsub = subscribe(
					`/api/media-jobs/${jobId}/events`,
					['message', 'done'],
					(type, data: any) => {
						if (type === 'message') {
							if (data?.stage) progressStage = data.stage;
							if (data?.message) progressMessage = data.message;
							// Drive the bar from the stage sequence:
							//   preflight→remux→validate→replace→done
							const stage = data?.stage;
							const state = data?.state;
							if (stage && state) {
								if (stage === 'preflight') progressPercent = state === 'start' ? 5 : 15;
								else if (stage === 'remux') progressPercent = state === 'start' ? 20 : 65;
								else if (stage === 'validate') progressPercent = state === 'start' ? 70 : 80;
								else if (stage === 'replace') progressPercent = 85;
								else if (stage === 'external') progressPercent = 92;
								else if (stage === 'done') progressPercent = 100;
								else if (stage === 'start') progressPercent = 1;
							}
						} else if (type === 'done') {
							unsub();
							const status = data?.status ?? '';
							if (status === 'succeeded' || status === 'completed' || !status) {
								progressPercent = 100;
								resolve(onDone());
							} else {
								reject(new Error(data?.error || `Job ended with status: ${status}`));
							}
						}
					}
				);
				// If the EventSource connection itself fails, reject so the UI
				// doesn't hang forever.
				setTimeout(() => {
					if (progressPercent < 100 && progressPercent > 0) {
						// The job may have finished while we were disconnected — poll once.
						fetch(`/api/media-jobs/${jobId}`)
							.then((r) => r.json())
							.then((j) => {
								if (j.status === 'succeeded' || j.status === 'completed') {
									unsub();
									progressPercent = 100;
									resolve(onDone());
								}
							})
							.catch(() => {});
					}
				}, 8000);
			});
		}

	function cleanupAndRefresh() {
		submitting = false;
		activeJobId = null;
		planResult = null;
		selectedOp = null;
		selectedIds.clear();
		onMutationComplete();
	}

	// Plan creation
	async function handleCreatePlan() {
		if (selectedTracks.length === 0 || !selectedOp) return;
		submitting = true;
		progressMessage = 'Generating mutation plan...';
		progressPercent = 10;

		try {
			let edits: any[] = [];
			if (selectedOp === 'metadata') {
				edits = selectedTracks.map(t => {
					const edit: any = { track_id: t.id };
					if (metaTitle.trim()) edit.title = metaTitle;
					if (metaLang.trim()) edit.language_tag = metaLang;
					if (metaDefault !== 'keep') edit.is_default = metaDefault === 'true';
					if (metaForced !== 'keep') edit.is_forced = metaForced === 'true';
					if (metaSdh !== 'keep') edit.is_sdh = metaSdh === 'true';
					if (metaCommentary !== 'keep') edit.is_commentary = metaCommentary === 'true';
					return edit;
				});
			}

			const planOp = selectedOp === 'remove' ? 'subtitle_remove' : selectedOp === 'embed' ? 'subtitle_embed' : 'subtitle_metadata';

			const plan = await createPlan(fetch, mediaFileId, {
				operation: planOp,
				track_ids: selectedTracks.map(t => t.id),
				edits,
				backup: true,
				allow_break: false
			});

			planResult = plan;
			submitting = false;
		} catch (e: any) {
			toast(e.message || 'Failed to build plan', 'bad');
			submitting = false;
		}
	}

	// Confirm plan execution (Mutation path)
	async function handleConfirmPlan() {
		if (!planResult) return;
		submitting = true;
		progressMessage = 'Submitting job...';
		progressPercent = 5;

		try {
			const res = await confirmJob(fetch, planResult.job_id);
			activeJobId = planResult.job_id;
			progressMessage = 'Waiting in queue...';

			await monitorJob(planResult.job_id, () => {
				toast('Mutation job completed successfully', 'good');
				cleanupAndRefresh();
			});
		} catch (e: any) {
			toast(`Job failed: ${e.message}`, 'bad');
			submitting = false;
		}
	}

		// Multi-step Extract execution
		async function handleExtract() {
			if (selectedTracks.length !== 1) return;
			const track = selectedTracks[0];
			submitting = true;
			progressMessage = `Extracting track ${track.language_tag.toUpperCase()}...`;
			progressPercent = 10;

			try {
				const res = await extractTrack(fetch, mediaFileId, track.id);
				activeJobId = res.job_id;
				progressMessage = 'Extraction started...';

				await monitorJob(res.job_id, async () => {
					if (deleteOriginalAfterExtract) {
						progressMessage = 'Extraction finished. Removing original embedded track (remuxing)...';
						progressPercent = 85;

						// Re-fetch the inventory — the extract handler rescans,
						// which replaces all track IDs.  Find the embedded track
						// by its stable attributes (tool_track_id + language).
						const fresh = await getInventory(fetch, mediaFileId);
						const embedded = fresh.tracks.find(
							(t: SubtitleTrack) =>
								t.source === 'embedded' &&
								t.tool_track_id === track.tool_track_id &&
								t.language_tag === track.language_tag
						);
						if (!embedded) {
							toast('Original track not found after extraction — it may already be gone.', 'info');
							cleanupAndRefresh();
							return;
						}

						const plan = await createPlan(fetch, mediaFileId, {
							operation: 'subtitle_remove',
							track_ids: [embedded.id],
							backup: true,
							allow_break: false
						});

						progressMessage = 'Confirming original track removal...';
						const confirmRes = await confirmJob(fetch, plan.job_id);
						activeJobId = plan.job_id;

						await monitorJob(plan.job_id, () => {
							toast('Track extracted and original embedded track removed successfully', 'good');
							cleanupAndRefresh();
						});
					} else {
						toast('Track extracted successfully', 'good');
						cleanupAndRefresh();
					}
				});
			} catch (e: any) {
				toast(`Extraction failed: ${e.message}`, 'bad');
				submitting = false;
			}
		}

	// Cancel running job
	async function handleCancelJob() {
		if (!activeJobId) return;
		try {
			await cancelJob(fetch, activeJobId);
			toast('Cancel requested', 'info');
		} catch (e: any) {
			toast(`Failed to cancel: ${e.message}`, 'bad');
		}
	}
</script>

{#if submitting}
	<div class="progress-overlay">
		<div class="progress-card">
			<h4>Executing Operation...</h4>
			<p class="stage">{progressStage || 'Running'}</p>
			<ProgressBar value={progressPercent} />
			<p class="msg">{progressMessage}</p>
			{#if activeJobId}
				<button class="btn-cancel" onclick={handleCancelJob}>Cancel Job</button>
			{/if}
		</div>
	</div>
{/if}

{#if planResult}
	<ConfirmDialog
		open={true}
		title="Review Mutation Plan"
		confirmLabel="Execute Plan"
		tone="gold"
		onConfirm={handleConfirmPlan}
		onCancel={() => planResult = null}
	>
		<PlanReview plan={planResult} />
	</ConfirmDialog>
{/if}

<div class="toolbar">
	<div class="toolbar-section selectors">
		<span class="lbl">Select:</span>
		<button onclick={() => selectSource('embedded')}>Embedded</button>
		<button onclick={() => selectSource('external')}>External</button>
		<button onclick={() => selectFlag('is_forced')}>Forced</button>
		<button onclick={() => selectFlag('is_sdh')}>SDH</button>
		<button onclick={() => selectFlag('is_commentary')}>Commentary</button>
		<button class="text-btn" onclick={() => selectedIds.clear()}>Clear</button>
	</div>
	<div class="toolbar-section text-filters">
		<div class="input-wrap">
			<input type="text" placeholder="Whitelist (e.g. en, ja)" bind:value={whitelistText} />
			<button onclick={applyWhitelist}>+</button>
		</div>
		<div class="input-wrap">
			<input type="text" placeholder="Blacklist (e.g. es, fr)" bind:value={blacklistText} />
			<button onclick={applyBlacklist}>-</button>
		</div>
	</div>
</div>

<div class="table-container">
	<table class="tracks-table">
		<thead>
			<tr>
				<th class="checkbox-cell">
					<input
						type="checkbox"
						checked={allSelected}
						onclick={toggleSelectAll}
					/>
				</th>
				<th>Source</th>
				<th>Language</th>
				<th>Codec</th>
				<th>Kind</th>
				<th>Flags</th>
				<th class="size-col">Size</th>
			</tr>
		</thead>
		<tbody>
			{#each tracks as track, idx}
				<TrackRow
					{track}
					selected={selectedIds.has(track.id)}
					onToggle={(e) => onRowToggle(idx, e)}
					onSelect={() => onRowSelect(idx)}
				/>
			{/each}
		</tbody>
	</table>
</div>

<div class="actions-section">
	{#if selectedTracks.length > 0}
		<div class="operation-picker">
			<span class="selected-count">{selectedTracks.length} track(s) selected</span>
			<div class="picker-btns">
				{#if selectedTracks.every(t => t.source === 'embedded')}
					<button
						class="action-btn"
						class:active={selectedOp === 'remove'}
						onclick={() => selectedOp = 'remove'}
						disabled={!capabilities.can_remove}
						title={!capabilities.can_remove ? 'Container capabilities do not allow removing tracks' : ''}
					>
						🗑️ Remove
					</button>
					{#if selectedTracks.length === 1}
						<button
							class="action-btn"
							class:active={selectedOp === 'extract'}
							onclick={() => selectedOp = 'extract'}
						>
							📦 Extract to Sidecar
						</button>
					{/if}
				{/if}
				{#if selectedTracks.every(t => t.source === 'external')}
					<button
						class="action-btn"
						class:active={selectedOp === 'embed'}
						onclick={() => selectedOp = 'embed'}
						disabled={!capabilities.can_embed_text}
						title={!capabilities.can_embed_text ? 'Container capabilities do not allow embedding tracks' : ''}
					>
						📥 Embed
					</button>
				{/if}
				<button
					class="action-btn"
					class:active={selectedOp === 'metadata'}
					onclick={() => selectedOp = 'metadata'}
					disabled={!capabilities.can_edit_metadata}
					title={!capabilities.can_edit_metadata ? 'Container capabilities do not allow editing metadata' : ''}
				>
					🏷️ Edit Metadata
				</button>
			</div>
		</div>

		{#if selectedOp === 'remove'}
			<div class="op-panel mq-rise">
				<p>Removes the selected embedded track(s) from the container. This operation remuxes the file via <code>mkvmerge</code> or <code>ffmpeg</code>, preserving audio/video streams at full untouched quality.</p>
				<div class="op-foot">
					<button class="btn primary" onclick={handleCreatePlan}>Create Removal Plan</button>
				</div>
			</div>
		{/if}

		{#if selectedOp === 'embed'}
			<div class="op-panel mq-rise">
				<p>Embeds selected external subtitle sidecars into the media container. Original sidecar files are deleted after completion by default. Audio/video streams remain untouched and at full quality.</p>
				<div class="op-foot">
					<button class="btn primary" onclick={handleCreatePlan}>Create Embed Plan</button>
				</div>
			</div>
		{/if}

		{#if selectedOp === 'extract'}
			<div class="op-panel mq-rise">
				<p>Extracts the selected embedded subtitle track into a Plex/Jellyfin compatible sidecar file next to the video. Audio/video streams remain untouched.</p>
				<div class="checkbox-option">
					<input type="checkbox" id="del-orig" bind:checked={deleteOriginalAfterExtract} />
					<label for="del-orig">Delete original embedded track after extraction (clean extract via remux)</label>
				</div>
				<div class="op-foot">
					<button class="btn primary" onclick={handleExtract}>Execute Extraction</button>
				</div>
			</div>
		{/if}

		{#if selectedOp === 'metadata'}
			<div class="op-panel mq-rise metadata-edit-panel">
				<div class="form-grid">
					<div class="field">
						<label for="meta-title">Title</label>
						<input type="text" id="meta-title" placeholder="e.g. English SDH" bind:value={metaTitle} />
					</div>
					<div class="field">
						<label for="meta-lang">Language Tag</label>
						<input type="text" id="meta-lang" placeholder="e.g. en, eng" bind:value={metaLang} />
					</div>
					<div class="field">
						<label for="meta-def">Default</label>
						<select id="meta-def" bind:value={metaDefault}>
							<option value="keep">Keep Current</option>
							<option value="true">True</option>
							<option value="false">False</option>
						</select>
					</div>
					<div class="field">
						<label for="meta-forced">Forced</label>
						<select id="meta-forced" bind:value={metaForced}>
							<option value="keep">Keep Current</option>
							<option value="true">True</option>
							<option value="false">False</option>
						</select>
					</div>
					<div class="field">
						<label for="meta-sdh">SDH</label>
						<select id="meta-sdh" bind:value={metaSdh}>
							<option value="keep">Keep Current</option>
							<option value="true">True</option>
							<option value="false">False</option>
						</select>
					</div>
					<div class="field">
						<label for="meta-comm">Commentary</label>
						<select id="meta-comm" bind:value={metaCommentary}>
							<option value="keep">Keep Current</option>
							<option value="true">True</option>
							<option value="false">False</option>
						</select>
					</div>
				</div>
				<div class="op-foot">
					<button class="btn primary" onclick={handleCreatePlan}>Create Edit Plan</button>
				</div>
			</div>
		{/if}
	{/if}
</div>

<style>
	.toolbar {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 10px;
		background: var(--panel2);
		border: 1px solid var(--line);
		border-bottom: none;
		border-radius: var(--radius-sm) var(--radius-sm) 0 0;
		gap: 12px;
		flex-wrap: wrap;
	}
	.toolbar-section {
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.selectors .lbl {
		font-size: 12px;
		color: var(--muted);
		margin-right: 4px;
	}
	.selectors button {
		background: var(--panel);
		border: 1px solid var(--line);
		padding: 4px 10px;
		font-size: 12px;
		border-radius: 4px;
		color: var(--text);
		cursor: pointer;
	}
	.selectors button:hover {
		background: var(--line);
	}
	.selectors .text-btn {
		background: transparent;
		border: none;
		color: var(--muted);
	}
	.selectors .text-btn:hover {
		color: var(--text);
		text-decoration: underline;
	}
	.text-filters {
		gap: 10px;
	}
	.input-wrap {
		display: flex;
		border: 1px solid var(--line);
		border-radius: 4px;
		overflow: hidden;
		background: var(--panel);
	}
	.input-wrap input {
		border: none;
		background: transparent;
		padding: 4px 8px;
		font-size: 11.5px;
		color: var(--text);
		width: 140px;
		outline: none;
	}
	.input-wrap button {
		border: none;
		background: var(--panel2);
		border-left: 1px solid var(--line);
		color: var(--text);
		padding: 4px 8px;
		cursor: pointer;
	}
	.input-wrap button:hover {
		background: var(--line);
	}

	.table-container {
		border: 1px solid var(--line);
		background: var(--panel);
		max-height: 240px;
		overflow-y: auto;
	}
	.tracks-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
	}
	th {
		padding: 8px 12px;
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
		font-weight: 700;
		background: var(--ink2);
		border-bottom: 1px solid var(--line);
		position: sticky;
		top: 0;
		z-index: 10;
	}
	.checkbox-cell {
		width: 32px;
		text-align: center;
	}
	.checkbox-cell input {
		width: 15px;
		height: 15px;
		cursor: pointer;
		accent-color: var(--gold);
	}
	.size-col {
		text-align: right;
	}

	.actions-section {
		margin-top: 14px;
	}
	.operation-picker {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 10px 14px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		flex-wrap: wrap;
		gap: 10px;
	}
	.selected-count {
		font-size: 13px;
		color: var(--gold);
		font-weight: 550;
	}
	.picker-btns {
		display: flex;
		gap: 6px;
	}
	.action-btn {
		background: var(--panel2);
		border: 1px solid var(--line);
		color: var(--text);
		padding: 6px 12px;
		border-radius: 6px;
		font-size: 12.5px;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.action-btn:hover:not(:disabled) {
		background: var(--line);
	}
	.action-btn.active {
		border-color: var(--gold);
		background: var(--panel2);
		color: var(--gold);
	}
	.action-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.op-panel {
		margin-top: 10px;
		background: var(--panel);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
	}
	.op-panel p {
		margin-top: 0;
		margin-bottom: 12px;
		font-size: 13px;
		color: var(--muted);
		line-height: 1.4;
	}
	.checkbox-option {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 14px;
		font-size: 13px;
		color: var(--text);
	}
	.checkbox-option input {
		width: 16px;
		height: 16px;
		accent-color: var(--gold);
		cursor: pointer;
	}
	.checkbox-option label {
		cursor: pointer;
	}
	.op-foot {
		display: flex;
		justify-content: flex-end;
	}

	/* Metadata form */
	.metadata-edit-panel .form-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 12px;
		margin-bottom: 14px;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 5px;
	}
	.field label {
		font-size: 11.5px;
		font-weight: 600;
		color: var(--faint2);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.field input, .field select {
		background: var(--panel2);
		border: 1px solid var(--line);
		border-radius: 5px;
		padding: 8px;
		font-size: 13px;
		color: var(--text);
		outline: none;
	}
	.field input:focus, .field select:focus {
		border-color: var(--gold);
	}

	/* Buttons */
	.btn {
		font-size: 13px;
		font-weight: 600;
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		cursor: pointer;
		border: none;
		transition: background-color 0.15s;
	}
	.btn.primary {
		background: var(--gold);
		color: var(--on-gold);
	}
	.btn.primary:hover {
		background: var(--gold-deep);
	}

	/* Progress Overlay */
	.progress-overlay {
		position: fixed;
		inset: 0;
		background: rgba(12, 13, 17, 0.7);
		backdrop-filter: blur(4px);
		z-index: 100;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 16px;
	}
	.progress-card {
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		padding: 24px;
		width: 100%;
		max-width: 400px;
		text-align: center;
		box-shadow: 0 10px 30px var(--shadow);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.progress-card h4 {
		margin: 0;
		font-size: 15px;
		color: var(--text);
	}
	.progress-card .stage {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--gold);
		text-transform: uppercase;
		margin: 0;
	}
	.progress-card .msg {
		font-size: 13px;
		color: var(--muted);
		margin: 0;
	}
	.btn-cancel {
		background: transparent;
		border: 1px solid var(--bad);
		color: var(--bad);
		padding: 6px 12px;
		border-radius: 6px;
		font-size: 12px;
		cursor: pointer;
		width: max-content;
		margin: 8px auto 0;
		transition: background-color 0.15s;
	}
	.btn-cancel:hover {
		background: rgba(239, 83, 80, 0.1);
	}
</style>
