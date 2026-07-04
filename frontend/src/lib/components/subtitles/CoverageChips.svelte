<script lang="ts">
	import type { AudioSubStatus } from '$lib/api/types';

	let { status }: { status: AudioSubStatus | 'none met' | string } = $props();

	const META: Record<string, { label: string; tone: string }> = {
		ok: { label: 'OK', tone: 'var(--good)' },
		gaps: { label: 'Gaps', tone: 'var(--warn)' },
		audio_gap: { label: 'Audio Gap', tone: 'var(--warn)' },
		subtitle_gap: { label: 'Subtitle Gap', tone: 'var(--info)' },
		both_gap: { label: 'Both Gap', tone: 'var(--bad)' },
		'none met': { label: 'None Met', tone: 'var(--bad)' },
		unknown: { label: 'Unknown', tone: 'var(--muted)' }
	};

	const resolved = $derived(META[status] || { label: status, tone: 'var(--muted)' });
</script>

<span class="status-chip" style={`--c: ${resolved.tone}`}>
	{resolved.label}
</span>

<style>
	.status-chip {
		font-size: 11px;
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 5px;
		color: var(--c);
		background: color-mix(in srgb, var(--c) 12%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 25%, transparent);
		white-space: nowrap;
		display: inline-flex;
		align-items: center;
	}
</style>
