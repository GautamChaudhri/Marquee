/** What the System page says: the settings in words, and what is wrong, in words. */
import type { SystemInfo, WorkerInfo } from '$lib/api/types';

/** Everything the page marks as a problem: a schema behind the code, a model file missing
 *  or a manifest that cannot be read, and a worker that is not running. */
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
	return found;
}

export function deployPolicyLine(policy: SystemInfo['settings']['deploy_policy']): string {
	switch (policy) {
		case 'off':
			return 'Off: a run’s pick is written only when you choose it';
		case 'missing':
			return 'Missing: a run’s pick is written where there is no poster, or Marquee’s own';
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
