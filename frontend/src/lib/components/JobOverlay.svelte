<script lang="ts">
	import type { JobTimelineItem } from '$lib/api/types';

	let {
		jobs,
		startAt,
		endAt,
		x = 0,
		y = 0,
		width,
		height = 16
	}: {
		jobs: JobTimelineItem[];
		startAt: number;
		endAt: number;
		x?: number;
		y?: number;
		width: number;
		height?: number;
	} = $props();

	const COLORS: Record<string, string> = {
		poster_pipeline: 'var(--gold)',
		poster_pipeline_batch: 'var(--gold-deep)',
		subtitle_generate: 'var(--good)',
		subtitle_scan: 'var(--good)',
		subtitle_remove: '#3fa9f5',
		audio_remove: '#0ea5a4',
		track_remove: '#1d9bf0',
		letterbox_reencode: '#f97316',
		letterbox_apply: '#fb923c',
		letterbox_remove: '#fdba74',
		backup_create: '#9a6b3c'
	};

	function colorFor(job: JobTimelineItem): string {
		return COLORS[job.type] ?? 'var(--accent)';
	}

	function clamp(value: number): number {
		return Math.min(1, Math.max(0, value));
	}

	function xFor(iso: string | null): number {
		if (!iso || endAt <= startAt) return x;
		const stamp = new Date(iso).getTime();
		const ratio = clamp((stamp - startAt) / (endAt - startAt));
		return x + ratio * width;
	}

	function widthFor(job: JobTimelineItem): number {
		const left = xFor(job.started_at);
		const right = xFor(job.finished_at ?? new Date(endAt).toISOString());
		return Math.max(3, right - left);
	}
</script>

<g class="overlay">
	{#each jobs as job (job.job_id)}
		{#if job.started_at}
			<a href={`/projection-room/jobs/${job.job_id}`}>
				<rect
					x={xFor(job.started_at)}
					{y}
					width={widthFor(job)}
					{height}
					fill={colorFor(job)}
					fill-opacity="0.18"
					stroke={colorFor(job)}
					stroke-opacity="0.55"
					rx="4"
				>
					<title>{job.label}{job.subject ? ` · ${job.subject}` : ''}</title>
				</rect>
			</a>
		{/if}
	{/each}
</g>
