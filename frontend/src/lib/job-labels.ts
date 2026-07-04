const JOB_LABELS: Record<string, string> = {
	poster_pipeline_tv_batch: 'TV poster batch',
	subtitle_scan_all: 'Subtitle Scan',
	subtitle_generate_batch: 'Subtitle Generation (TV Batch)',
	subtitle_generate: 'Subtitle Generation'
};

export function humanizeJobType(type: string): string {
	return JOB_LABELS[type]
		? JOB_LABELS[type]
		: type
				.split('_')
				.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
				.join(' ');
}

export function displayJobLabel(job: {
	label?: string | null;
	type: string;
	payload?: Record<string, unknown> | null;
	subject?: { type: string } | null;
}): string {
	const library = job.payload?.library;
	if (job.type === 'taste_rebuild' && library === 'tv') return 'Taste rebuild (TV)';
	if (job.type === 'taste_map' && library === 'tv') return 'Taste map (TV)';
	if (job.type === 'learned_head_train' && library === 'tv') return 'Taste head train (TV)';
	if (job.subject?.type === 'dovi_tv_batch') return 'DoVi analysis (TV)';

	const scope = job.payload?.scope;
	if (job.type === 'subtitle_scan_all') {
		if (scope === 'movies') return 'Subtitle Scan (Movies)';
		if (scope === 'tv') return 'Subtitle Scan (TV)';
		if (scope === 'series') return 'Subtitle Scan (Show)';
		return 'Subtitle Scan';
	}

	return job.label || humanizeJobType(job.type);
}

export function displayMediaJobLabel(job: { label?: string | null; operation: string }): string {
	return job.label || humanizeJobType(job.operation);
}
