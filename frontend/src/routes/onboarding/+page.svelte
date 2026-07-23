<script lang="ts">
	import { goto } from '$app/navigation';
	import FeatureActivityPanel from '$lib/activity/components/FeatureActivityPanel.svelte';
	import ProgressBar from '$lib/components/ProgressBar.svelte';
	import SectionHeader from '$lib/components/SectionHeader.svelte';
	import { completeOnboarding, startOnboarding } from '$lib/api/onboarding';
	import type { OnboardingStatus } from '$lib/api/types';
	import { toast } from '$lib/toast';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// svelte-ignore state_referenced_locally
	let status = $state<OnboardingStatus | null>(data.status);
	let busy = $state(false);
	// svelte-ignore state_referenced_locally
	let initiatedJobIds = $state<string[]>(status?.active_jobs.map((job) => job.job_id) ?? []);

	const count = $derived(status?.active_positive_subjects ?? 0);
	const required = $derived(status?.thresholds.required ?? 50);
	const encouraged = $derived(status?.thresholds.encouraged ?? 75);
	const strongTarget = $derived(status?.thresholds.strong_target ?? 100);
	const personalized = $derived(status?.state === 'personalized');
	const canBuild = $derived(count >= required && status?.state !== 'building');

	async function analyzeNext() {
		busy = true;
		try {
			const result = await startOnboarding(fetch);
			status = result.status;
			initiatedJobIds = [result.analysis_job.job_id, ...initiatedJobIds].slice(0, 20);
			toast(`Analyzing neutral choices for ${result.subject.title}`, 'good');
			void goto(result.analysis_job.detail_url);
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not analyze the next movie', 'bad');
		} finally {
			busy = false;
		}
	}

	async function buildProfiles() {
		busy = true;
		try {
			const result = await completeOnboarding(fetch);
			status = result.status;
			initiatedJobIds = result.build_jobs.map((job) => job.job_id);
			toast('Building and validating your movie and TV taste profiles', 'good');
		} catch (error) {
			toast(error instanceof Error ? error.message : 'Could not start the profile build', 'bad');
		} finally {
			busy = false;
		}
	}
</script>

<SectionHeader
	title="Teach Marquee your poster taste"
	subtitle="Choose real posters from your library; Marquee is filtering, not ranking, until it learns enough"
/>

{#if data.error}
	<div class="notice bad">{data.error}</div>
{:else if status}
	<div class="journey">
		<div class="count"><strong>{count} of {required}</strong> required choices</div>
		<ProgressBar
			value={Math.min(100, (count / strongTarget) * 100)}
			tone={count >= required ? 'good' : 'gold'}
			height={8}
		/>
		<div class="milestones">
			<span class:reached={count >= required}>{required} required</span>
			<span class:reached={count >= encouraged}>{encouraged} recommended</span>
			<span class:reached={count >= strongTarget}>{strongTarget} strong target</span>
		</div>
		<p>{status.next_action}</p>
		<p class="fine">
			Only an explicit choice followed by a successful, validated poster deployment counts.
			Duplicate movies or identical poster bytes do not increase this total. Automatic picks never
			teach Marquee.
		</p>
	</div>

	{#if personalized}
		<div class="notice good">
			<strong>Your taste profiles are active.</strong> Both movie and TV consumers reloaded the published
			profile revision. You can keep refining your taste here at any time.
		</div>
	{:else if status.state === 'building'}
		<div class="notice">
			Marquee is building, validating, publishing, and reloading both profiles.
		</div>
	{:else if status.state === 'degraded'}
		<div class="notice bad">
			The latest build failed. Your evidence is safe; retry from this page.
		</div>
	{/if}

	<div class="actions">
		<button class="btn-gold" onclick={analyzeNext} disabled={busy || status.state === 'building'}>
			{busy ? 'Working…' : 'Choose for another movie'}
		</button>
		{#if canBuild}
			<button class="btn-secondary" onclick={buildProfiles} disabled={busy}
				>Build taste profiles</button
			>
		{/if}
		<a class="link" href="/activity?feature_area=ml_taste">Open Activity and evidence →</a>
	</div>

	<FeatureActivityPanel
		scopeKey="feature:onboarding:taste"
		query={{ feature_area: 'ml_taste' }}
		jobIds={initiatedJobIds}
		heading="Taste onboarding activity"
	/>
{/if}

<style>
	.journey,
	.notice {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--panel);
		padding: 16px 18px;
		margin-bottom: 16px;
	}
	.journey {
		background: var(--gold-soft);
	}
	.count {
		margin-bottom: 10px;
		font-size: 18px;
		color: var(--gold);
	}
	.milestones {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		margin-top: 7px;
		font-size: 11px;
		color: var(--faint);
	}
	.milestones .reached {
		color: var(--good);
		font-weight: 650;
	}
	.fine {
		margin-bottom: 0;
		font-size: 12px;
		line-height: 1.5;
		color: var(--muted);
	}
	.notice.good {
		border-color: color-mix(in srgb, var(--good) 45%, var(--line));
	}
	.notice.bad {
		border-color: color-mix(in srgb, var(--bad) 45%, var(--line));
	}
	.actions {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: 18px;
	}
</style>
