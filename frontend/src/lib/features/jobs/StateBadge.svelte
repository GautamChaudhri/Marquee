<script lang="ts">
	import type { JobState } from '$lib/api/types';

	let { state }: { state: JobState } = $props();
</script>

<span class="badge {state}">{state}</span>

<style>
	.badge {
		display: inline-block;
		padding: 1px 8px;
		border-radius: var(--radius-pill);
		border: 1px solid var(--line2);
		font-size: 11.5px;
		text-transform: capitalize;
		white-space: nowrap;
		color: var(--muted);
	}
	.queued {
		color: var(--queue);
		border-color: color-mix(in srgb, var(--queue) 50%, transparent);
	}
	.running {
		color: var(--info);
		border-color: color-mix(in srgb, var(--info) 50%, transparent);
	}
	.succeeded {
		color: var(--good);
		border-color: color-mix(in srgb, var(--good) 45%, transparent);
	}
	.failed {
		color: var(--bad);
		border-color: color-mix(in srgb, var(--bad) 50%, transparent);
	}
	.cancelled {
		color: var(--faint);
	}
</style>
