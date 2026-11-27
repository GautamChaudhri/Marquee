<script lang="ts">
	import { onMount } from 'svelte';
	import { api, call } from '#lib/api/client.ts';
	import type { SystemInfo, WorkerInfo } from '#lib/api/types.ts';
	import StateBadge from '#lib/features/jobs/StateBadge.svelte';
	import {
		backupLine,
		backupSettingLine,
		countsLine,
		TMDB_NOTICE,
		deployPolicyLine,
		pathsNote,
		problems,
		retentionLine,
		retentionSettingLine,
		syncLine
	} from '#lib/features/system/system.ts';
	import { exemplarCounts } from '#lib/features/taste/taste.ts';
	import { formatBytes, formatWhen } from '#lib/format.ts';
	import Button from '#lib/ui/Button.svelte';
	import Notice from '#lib/ui/Notice.svelte';

	/** What this installation is and how it is set. Read-only: nothing here changes a setting. */
	let system = $state<SystemInfo | null>(null);
	let worker = $state<WorkerInfo | null>(null);
	let error = $state<string | null>(null);
	let workerError = $state<string | null>(null);

	async function load() {
		error = null;
		workerError = null;
		const [summary, workerResult] = await Promise.all([
			call(api.GET('/api/system')),
			call(api.GET('/api/worker'))
		]);
		if (summary.ok) system = summary.data;
		else error = summary.message;
		if (workerResult.ok) worker = workerResult.data;
		else workerError = workerResult.message;
	}

	onMount(() => {
		void load();
	});

	const found = $derived(system ? problems(system, worker) : []);
	const inContainers = $derived(system ? pathsNote(system.environment) !== null : false);
</script>

<svelte:head><title>System · Marquee</title></svelte:head>

{#if error}
	<Notice tone="bad" message="The system summary could not be loaded. {error}">
		{#snippet action()}<Button onclick={load}>Try again</Button>{/snippet}
	</Notice>
{:else if !system}
	<Notice tone="loading" message="Loading the system summary…" />
{:else}
	{#if found.length > 0}
		<section class="problems" data-testid="problems" aria-label="Problems">
			<h2>Needs attention</h2>
			<ul>
				{#each found as problem (problem)}<li>{problem}</li>{/each}
			</ul>
		</section>
	{:else}
		<p class="ok" data-testid="no-problems">Nothing needs attention.</p>
	{/if}

	<div class="tables">
		<section>
			<h2>Installation</h2>
			<table>
				<tbody>
					<tr><th>Version</th><td>{system.version}</td></tr>
					<tr><th>Environment</th><td>{system.environment}</td></tr>
					<tr>
						<th>Database schema</th>
						<td data-testid="schema" class:bad={!system.schema.at_head}>
							{system.schema.current ?? 'none'}
							{system.schema.at_head
								? '(the newest)'
								: `(behind: this code needs ${system.schema.head})`}
						</td>
					</tr>
					<tr>
						<th>Worker</th>
						<td data-testid="worker" class:bad={worker !== null && !worker.running}>
							{#if workerError}
								Unknown: {workerError}
							{:else if !worker}
								…
							{:else if worker.running}
								Running since {formatWhen(worker.since)}; {worker.queued} queued, {worker.running_jobs}
								running, {worker.failed} failed
							{:else}
								Not running; {worker.queued} queued
							{/if}
						</td>
					</tr>
					<tr>
						<th>Last sync</th>
						<td>
							{#if system.last_sync}
								<a href="/activity?job={system.last_sync.job_id}">Job {system.last_sync.job_id}</a>
								<StateBadge state={system.last_sync.state} />
								{formatWhen(system.last_sync.finished_at ?? system.last_sync.created_at)}
							{:else}
								Never
							{/if}
						</td>
					</tr>
					<tr>
						<th>Database backup</th>
						<td data-testid="backup" class:bad={system.backups.stale}>
							{backupLine(system.backups)}
							{#if system.backups.last_job}
								<br /><a href="/activity?job={system.backups.last_job.job_id}"
									>Job {system.backups.last_job.job_id}</a
								>
								<StateBadge state={system.backups.last_job.state} />
							{/if}
						</td>
					</tr>
					<tr>
						<th>Retention</th>
						<td data-testid="retention">
							{retentionLine(system.retention)}
							{#if system.retention.last_job}
								<br /><a href="/activity?job={system.retention.last_job.job_id}"
									>Job {system.retention.last_job.job_id}</a
								>
								<StateBadge state={system.retention.last_job.state} />
							{/if}
						</td>
					</tr>
				</tbody>
			</table>
		</section>

		<section>
			<h2>Settings</h2>
			<table data-testid="settings">
				<tbody>
					<tr><th>Deploy policy</th><td>{deployPolicyLine(system.settings.deploy_policy)}</td></tr>
					<tr>
						<th>Run new movies</th>
						<td>{system.settings.run_new_movies ? 'Yes, after each sync' : 'No'}</td>
					</tr>
					<tr><th>Library sync</th><td>{syncLine(system.settings.sync_interval_minutes)}</td></tr>
					<tr><th>Database backup</th><td>{backupSettingLine(system.backups)}</td></tr>
					<tr><th>Retention</th><td>{retentionSettingLine(system.retention)}</td></tr>
					<tr>
						<th>Movie folders{inContainers ? ' (in the containers)' : ''}</th>
						<td>
							{#each system.settings.movie_roots as root (root)}<code>{root}</code><br
								/>{:else}none{/each}
						</td>
					</tr>
					<tr>
						<th>Data directory{inContainers ? ' (in the containers)' : ''}</th>
						<td><code>{system.settings.data_dir}</code></td>
					</tr>
					{#if pathsNote(system.environment)}
						<tr>
							<td colspan="2" class="note" data-testid="paths-note">
								{pathsNote(system.environment)}
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</section>

		<section>
			<h2>Library</h2>
			<table data-testid="library">
				<tbody>
					<tr><th>Movies</th><td>{system.library.movies}</td></tr>
					<tr><th>With a file</th><td>{system.library.movies_with_file}</td></tr>
					<tr><th>Posters</th><td>{countsLine(system.library.posters)}</td></tr>
					<tr><th>Runs</th><td>{countsLine(system.library.runs)}</td></tr>
					<tr>
						<th>Blind runs</th>
						<td data-testid="masked-runs">{countsLine(system.library.masked_runs)}</td>
					</tr>
					<tr><th>Deployments</th><td>{system.library.deployments}</td></tr>
					<tr><th>Exemplars</th><td>{exemplarCounts(system.library.exemplars)}</td></tr>
					<tr><th>Answers</th><td>{system.library.preference_events}</td></tr>
				</tbody>
			</table>
		</section>

		<section>
			<h2>Storage</h2>
			<table data-testid="storage">
				<tbody>
					<tr><th>Runs</th><td>{formatBytes(system.storage.runs)}</td></tr>
					<tr><th>Backups</th><td>{formatBytes(system.storage.backups)}</td></tr>
					<tr><th>Caches</th><td>{formatBytes(system.storage.cache)}</td></tr>
					<tr><th>Taste data</th><td>{formatBytes(system.storage.ml)}</td></tr>
					<tr><th>Measured</th><td>{formatWhen(system.storage.measured_at)}</td></tr>
				</tbody>
			</table>
		</section>
	</div>

	<section class="models">
		<h2>Model files</h2>
		{#if system.models.manifest !== 'found'}
			<Notice
				tone="bad"
				message={system.models.manifest === 'missing'
					? 'There is no model manifest.'
					: 'The model manifest cannot be read.'}
			/>
		{:else}
			<table data-testid="models" class="wide">
				<thead>
					<tr><th>File</th><th>For</th><th>Size</th></tr>
				</thead>
				<tbody>
					{#each system.models.files as file (file.path)}
						<tr class:bad={!file.present}>
							<td><code>{file.path}</code></td>
							<td>{file.item}</td>
							<td>
								{#if !file.present}
									Missing ({formatBytes(file.expected_size_bytes)} expected)
								{:else if file.size_bytes !== file.expected_size_bytes}
									{formatBytes(file.size_bytes)} ({formatBytes(file.expected_size_bytes)} expected)
								{:else}
									{formatBytes(file.size_bytes)}
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
{/if}

<section class="credits" data-testid="credits">
	<h2>Credits</h2>
	<p class="tmdb">
		<img src="/tmdb-logo.svg" alt="TMDB" width="91" height="12" />
		<span>{TMDB_NOTICE} Movie details and posters come from TMDB.</span>
	</p>
	<p>
		Marquee is free software under the GNU GPL v3. The models it downloads come under their own
		licences; with the default models an installation is for personal, non-commercial use (see
		docs/third-party.md in the source).
	</p>
</section>

<style>
	.problems {
		margin-bottom: 18px;
		padding: 12px 16px;
		border-radius: var(--radius);
		border: 1px solid color-mix(in srgb, var(--bad) 45%, transparent);
		background: color-mix(in srgb, var(--bad) 8%, var(--panel));
	}
	.problems h2 {
		color: var(--bad);
	}
	.problems ul {
		margin: 0;
		padding-left: 18px;
		font-size: 13px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.ok {
		margin: 0 0 18px;
		font-size: 13px;
		color: var(--good);
	}
	.tables {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 18px 24px;
	}
	h2 {
		margin: 0 0 8px;
		font-size: 15px;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	th,
	td {
		padding: 6px 8px;
		border-bottom: 1px solid var(--line);
		text-align: left;
		vertical-align: top;
		overflow-wrap: anywhere;
	}
	tbody th {
		width: 38%;
		font-weight: 500;
		color: var(--muted);
	}
	thead th {
		font-weight: 550;
		font-size: 12px;
		color: var(--muted);
	}
	td.bad,
	tr.bad td {
		color: var(--bad);
	}
	td a {
		color: var(--gold-copy);
		margin-right: 6px;
	}
	code {
		font-family: var(--font-mono);
		font-size: 12px;
	}
	td.note {
		color: var(--muted);
		font-size: 12px;
	}
	.models {
		margin-top: 22px;
	}
	.credits {
		margin-top: 22px;
		font-size: 12px;
		color: var(--muted);
	}
	.credits p {
		margin: 0 0 6px;
	}
	.tmdb {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
	}
	@media (max-width: 900px) {
		.tables {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
