const JOB_LABELS: Record<string, string> = {
	poster_pipeline_tv_batch: 'TV poster batch'
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
	return job.label || humanizeJobType(job.type);
}

export function displayMediaJobLabel(job: { label?: string | null; operation: string }): string {
	return job.label || humanizeJobType(job.operation);
}
