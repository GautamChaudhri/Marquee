/** What the System page says: the settings in words, and what is wrong, in words. */
import type { SystemInfo, WorkerInfo } from '#lib/api/types.ts';
import { formatBytes, formatWhen } from '#lib/format.ts';

type Backups = SystemInfo['backups'];
type Retention = SystemInfo['retention'];

/** Everything the page marks as a problem: a schema behind the code, a model file missing
 *  or a manifest that cannot be read, a worker that is not running, a database dump that is
 *  missing or overdue, and a backup or retention job that failed last time. */
export function problems(system: SystemInfo, worker: WorkerInfo | null): string[] {
	const found: string[] = [];
	if (!system.schema.at_head) {
		found.push(
			`The database schema is at ${system.schema.current ?? 'no revision'}, and this code needs ${system.schema.head}. Run “marquee db upgrade”.`
		);
	}
	if (system.models.manifest === 'missing') {
		found.push('There is no model manifest. Run “marquee models fetch”.');
	} else if (system.models.manifest === 'unreadable') {
		found.push('The model manifest cannot be read.');
	}
	for (const file of system.models.files.filter((f) => !f.present)) {
		found.push(`The model file ${file.path} is missing. Run “marquee models fetch”.`);
	}
	if (worker && !worker.running) {
		found.push('No worker is running, so nothing queued will run. Start “marquee worker”.');
	}
	const backups = system.backups;
	if (backups.stale) {
		found.push(
			backups.newest_at
				? `The newest database dump is from ${formatWhen(backups.newest_at)}, older than two intervals (${2 * backups.interval_hours} h). Is the backup job failing?`
				: 'No database dump has been made yet. The worker makes one when it starts; “marquee db backup” makes one now.'
		);
	}
	if (backups.last_job?.state === 'failed') {
		found.push(
			`The last database backup (job ${backups.last_job.job_id}) failed. Its error is on the Activity page.`
		);
	}
	const retention = system.retention.last_job;
	if (retention?.state === 'failed') {
		found.push(
			`The last retention pass (job ${retention.job_id}) failed. Its error is on the Activity page.`
		);
	}
	return found;
}

/** The newest dump in words: when, how big, and how many are kept. */
export function backupLine(backups: Backups): string {
	if (!backups.newest_at) return 'No dump yet';
	const count = backups.count === 1 ? '1 dump' : `${backups.count} dumps`;
	return `Newest ${formatWhen(backups.newest_at)} (${formatBytes(backups.newest_size_bytes)}); ${count}, ${formatBytes(backups.total_bytes)} in all`;
}

/** What the last retention pass freed, or that it has not run yet. */
export function retentionLine(retention: Retention): string {
	if (retention.freed_at === null || retention.freed_items === null) {
		return retention.interval_hours > 0
			? `Not run yet: the first pass is one interval (${retention.interval_hours} h) after the worker first starts with it`
			: 'Not run yet';
	}
	const items = retention.freed_items === 1 ? '1 item' : `${retention.freed_items} items`;
	return `Last pass ${formatWhen(retention.freed_at)}: deleted ${items}, ${formatBytes(retention.freed_bytes ?? 0)}`;
}

export function backupSettingLine(backups: Backups): string {
	if (backups.interval_hours <= 0) return 'Off: only the dumps you make with “marquee db backup”';
	return `Every ${backups.interval_hours} h; the newest ${backups.keep} kept`;
}

export function retentionSettingLine(retention: Retention): string {
	if (retention.interval_hours <= 0) return 'Off: nothing is deleted by itself';
	return `Every ${retention.interval_hours} h; finished jobs and unneeded runs after ${retention.days} days`;
}

export function deployPolicyLine(policy: SystemInfo['settings']['deploy_policy']): string {
	switch (policy) {
		case 'off':
			return 'Off: a run’s pick is written only when you choose it';
		case 'missing':
			return 'Missing: a run’s pick is written where there is no poster, or over its own earlier pick; never over a poster you chose';
		case 'all':
			return 'All: a run’s pick is written over any poster, yours included';
	}
}

export function syncLine(minutes: number): string {
	if (minutes <= 0) return 'Off';
	if (minutes % 60 === 0) return `Every ${minutes / 60} h`;
	return `Every ${minutes} min`;
}

/** "library 147, marquee 1" for a count by key, or "none". */
export function countsLine(counts: Record<string, number>): string {
	const entries = Object.entries(counts);
	if (entries.length === 0) return 'none';
	return entries.map(([key, n]) => `${key} ${n}`).join(', ');
}
