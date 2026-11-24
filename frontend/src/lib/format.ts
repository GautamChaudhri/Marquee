/** How dates, sizes, and scores read on the pages. */

/** A moment in the reader's own time zone, to the minute. */
export function formatWhen(iso: string | null | undefined, locale?: string): string {
	if (!iso) return '—';
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return '—';
	return date.toLocaleString(locale, {
		year: 'numeric',
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
}

/** How long something took, from two moments. */
export function formatDuration(
	start: string | null | undefined,
	end: string | null | undefined
): string {
	if (!start || !end) return '—';
	const ms = new Date(end).getTime() - new Date(start).getTime();
	if (!Number.isFinite(ms) || ms < 0) return '—';
	const seconds = Math.round(ms / 1000);
	if (seconds < 60) return `${seconds} s`;
	const minutes = Math.floor(seconds / 60);
	return `${minutes} min ${seconds % 60} s`;
}

export function formatBytes(bytes: number | null | undefined): string {
	if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return '—';
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

export function formatScore(score: number | null | undefined): string {
	return score === null || score === undefined || !Number.isFinite(score) ? '—' : score.toFixed(3);
}
