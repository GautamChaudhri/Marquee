import type { PageLoad } from './$types';
import { getJobMetrics, getJobMetricsByType, listJobs } from '$lib/api/jobs';
import type { JobListItem, JobMetrics, JobTypeStats } from '$lib/api/jobs';
import { getMetrics } from '$lib/api/system';
import type { SystemMetrics } from '$lib/api/types';

const EMPTY_JOBS: { jobs: JobListItem[]; next_before: number | null } = {
	jobs: [],
	next_before: null
};
const EMPTY_JOB_METRICS: JobMetrics = { counts: {}, resources: [], workers: [] };

export const load: PageLoad = async ({ fetch }) => {
	const safe = <T>(p: Promise<T>, fallback: T): Promise<T> => p.catch(() => fallback);
	// `running`/`queued` are how the Live tab finds already-active jobs
	// server-side (design/21 ingredient #2) — no flash-of-empty on load.
	const [running, queued, jobMetrics, byType, hostMetrics] = await Promise.all([
		safe(listJobs(fetch, { active: true, limit: 50 }), EMPTY_JOBS),
		safe(listJobs(fetch, { queued_only: true, limit: 50 }), EMPTY_JOBS),
		safe<JobMetrics>(getJobMetrics(fetch), EMPTY_JOB_METRICS),
		safe<{ by_type: Record<string, JobTypeStats> }>(getJobMetricsByType(fetch), { by_type: {} }),
		safe<SystemMetrics | null>(getMetrics(fetch), null)
	]);
	return { running, queued, jobMetrics, byType, hostMetrics };
};
