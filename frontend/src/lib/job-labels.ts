const JOB_LABELS: Record<string, string> = {
	poster_pipeline_tv_batch: 'TV poster batch',
	subtitle_scan: 'Subtitle Scan',
	subtitle_scan_all: 'Subtitle Scan',
	subtitle_generate_batch: 'Subtitle Generation (TV Batch)',
	subtitle_generate: 'Subtitle Generation',
	audio_remove: 'Audio Track Removal',
	track_remove: 'Track Removal',
	subtitle_remove: 'Subtitle Removal',
	subtitle_embed: 'Subtitle Embed',
	subtitle_metadata: 'Subtitle Metadata Update',
	subtitle_extract: 'Subtitle Extract',
	subtitle_policy: 'Subtitle Policy',
	subtitle_restore: 'Subtitle Restore',
	letterbox_detect_tv_batch: 'TV Letterbox Detection (Batch)',
	letterbox_detect_tv_scope: 'TV Letterbox Detection',
	letterbox_apply_tv_scope: 'TV Letterbox Apply',
	letterbox_revert_tv_scope: 'TV Letterbox Revert',
	letterbox_reencode_tv_batch: 'TV Letterbox Re-encode (Batch)',
	letterbox_reencode: 'Letterbox Re-encode'
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
	if (job.type === 'taste_enrich' && library === 'tv') return 'Taste enrichment (TV)';
	if (job.type === 'ranking_residual_train' && library === 'tv') return 'Taste residual train (TV)';
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
