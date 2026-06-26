export function humanizeJobType(type: string): string {
	return type
		.split('_')
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

export function displayJobLabel(job: { label?: string | null; type: string }): string {
	return job.label || humanizeJobType(job.type);
}

export function displayMediaJobLabel(job: { label?: string | null; operation: string }): string {
	return job.label || humanizeJobType(job.operation);
}
