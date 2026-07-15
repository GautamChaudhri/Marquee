<script lang="ts">
	import type { SubtitlePlan } from '$lib/api/types';

	let { plan }: { plan: SubtitlePlan } = $props();
</script>

<div class="plan-review">
	<div class="diff-section">
		<h5>Canonical mutation plan</h5>
		<div class="metrics">
			<div class="metric">
				<span class="label">Operation</span>
				<span class="val">{plan.operation.replace(/_/g, ' ')}</span>
			</div>
			<div class="metric">
				<span class="label">Plan version</span>
				<span class="val mono">{plan.plan_version.slice(0, 16)}…</span>
			</div>
			<div class="metric">
				<span class="label">Source signature</span>
				<span class="val mono">{plan.input_signature.slice(0, 16)}…</span>
			</div>
			<div class="metric">
				<span class="label">Valid until</span>
				<span class="val">{new Date(plan.plan_expires_at).toLocaleString()}</span>
			</div>
		</div>
		<p class="space-warning">Confirmation revalidates this exact plan before any work is queued.</p>
	</div>
</div>

<style>
	.plan-review {
		display: flex;
		flex-direction: column;
		gap: 16px;
		max-height: 70vh;
		overflow-y: auto;
		text-align: left;
	}
	h5 {
		margin-top: 0;
		margin-bottom: 8px;
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--faint2);
	}
	.diff-section {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 14px;
		background: var(--panel2);
	}
	.metrics {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 12px;
	}
	.metric {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.metric .label {
		font-size: 11px;
		color: var(--muted);
	}
	.metric .val {
		font-size: 13.5px;
		font-weight: 600;
		font-family: var(--font-mono);
	}
	.space-warning {
		margin-top: 10px;
		margin-bottom: 0;
		font-size: 12px;
		color: var(--muted);
	}
</style>
