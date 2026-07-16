import type { JobPresentation, JobRow, JobSnapshotResponse, PresentationSubject } from '../types';

export const subjects: Record<string, PresentationSubject> = {
	movie: subject('movie', 'Dune: Part Two', ['2024', 'Movie']),
	series: subject('series', 'Severance', ['Series']),
	season: subject('season', 'Severance · Season 2', ['Severance', 'Season 2']),
	episode: subject('episode', 'Hello, Ms. Cobel', ['Severance', 'S02E01']),
	mediaFile: subject('media_file', 'Severance.S02E01.mkv', ['Media file', 'Matroska']),
	track: subject('track', 'English subtitles', ['Subtitle track', 'English']),
	poster: subject('poster_generation', 'Dune poster', ['Poster', 'Candidate 3']),
	model: subject('model_profile_training', 'Taste profile', ['Model', 'Training']),
	maintenance: subject('maintenance', 'Library maintenance', ['System']),
	parentBatch: subject('batch', 'Refresh movie posters', ['Batch', '18 movies'])
};

function subject(kind: string, displayName: string, context: string[]): PresentationSubject {
	return {
		kind,
		display_id: `${kind}-1`,
		display_name: displayName,
		artwork_key: null,
		context,
		snapshot_at: '2026-07-16T12:00:00Z',
		missing_live_subject: false
	};
}

export function makeRow(overrides: Partial<JobRow> = {}): JobRow {
	return {
		version: 1,
		job_id: 'job-public-1',
		job_type: 'poster_pipeline',
		label: 'Refresh artwork',
		label_key: 'jobs.poster.refresh',
		feature_area: 'ai_posters',
		presentation_family: 'poster',
		subject: subjects.movie,
		action_headline: 'Selecting the best artwork',
		status: {
			label: 'Running',
			label_key: 'jobs.status.running',
			phase: 'running',
			outcome: null,
			tone: 'active'
		},
		attention: {
			level: 'normal',
			reason: 'none',
			message: null,
			remediation: null
		},
		trigger: { kind: 'manual', label: 'Started manually', initiator: 'Operator' },
		progress: {
			sequence: 4,
			headline: 'Comparing candidates',
			stage_key: 'rank_candidates',
			stage_label: 'Comparing artwork',
			freshness: 'live',
			updated_at: '2026-07-16T12:01:00Z',
			overall: {
				scope_id: 'movie-10',
				mode: 'determinate',
				label: 'Overall',
				percent: 37,
				completed: 3,
				total: 8,
				unit: 'steps'
			},
			current: null,
			current_subject: null,
			wait: null
		},
		priority: 0,
		fence_token: 0,
		execution_class: 'gpu',
		queue_rank: 1,
		is_parent: false,
		allowed_actions: ['cancel', 'open_detail'],
		links: {
			detail: '/api/jobs/job-public-1',
			presentation: '/api/jobs/job-public-1/presentation',
			snapshot: '/api/jobs/job-public-1/snapshot'
		},
		parent_id: null,
		root_id: 'job-public-1',
		retry_of_job_id: null,
		created_at: '2026-07-16T12:00:00Z',
		eligible_at: null,
		started_at: '2026-07-16T12:00:01Z',
		terminal_at: null,
		duration_seconds: null,
		impact: null,
		evidence: { artifacts_available: false, logs_available: false },
		...overrides
	};
}

export function makeSnapshot(
	row: JobRow,
	overrides: Partial<JobSnapshotResponse> = {}
): JobSnapshotResponse {
	return {
		version: 1,
		job_id: row.job_id,
		type: row.job_type,
		label: row.label,
		phase: row.status.phase,
		outcome: row.status.outcome ?? null,
		desired_state: 'run',
		fence_token: 7,
		execution_class: row.execution_class,
		progress_sequence: row.progress?.sequence ?? 0,
		progress: row.progress ?? null,
		status: row.status,
		attention: row.attention,
		priority: row.priority,
		allowed_actions: row.allowed_actions,
		links: row.links,
		configuration_version: 1,
		eligible_at: row.eligible_at ?? null,
		created_at: row.created_at ?? null,
		started_at: row.started_at ?? null,
		terminal_at: row.terminal_at ?? null,
		parent_id: row.parent_id ?? null,
		root_id: row.root_id ?? null,
		retry_of_job_id: row.retry_of_job_id ?? null,
		updated_at: '2026-07-16T12:01:00Z',
		last_event_id: 42,
		...overrides
	};
}

export function makePresentation(
	row: JobRow,
	overrides: Partial<JobPresentation> = {}
): JobPresentation {
	return {
		version: 1,
		job_id: row.job_id,
		job_type: row.job_type,
		label: row.label,
		label_key: row.label_key,
		feature_area: row.feature_area,
		presentation_family: row.presentation_family,
		presenter_key: 'poster.default',
		presenter_version: 1,
		subject: row.subject,
		action: { headline: row.action_headline, explanation: null },
		status: row.status,
		attention: row.attention,
		trigger: row.trigger,
		progress: row.progress ?? null,
		impact: row.impact ?? null,
		evidence: row.evidence,
		allowed_actions: [...row.allowed_actions, 'open_logs', 'open_artifacts'],
		links: {
			detail: row.links.detail,
			presentation: row.links.presentation,
			snapshot: row.links.snapshot,
			attempts: `/api/jobs/${row.job_id}/attempts`,
			events: `/api/jobs/${row.job_id}/events`,
			artifacts: `/api/jobs/${row.job_id}/artifacts`,
			children: null,
			raw_request: null,
			raw_plan: null,
			raw_result: null,
			raw_error: null
		},
		sections: [
			{
				kind: 'metric_cards',
				title: 'Performance',
				cards: [
					{ label: 'Elapsed', value: { type: 'duration', seconds: 12 } },
					{ label: 'Throughput', value: { type: 'number', value: 24, unit: 'frames/s' } }
				]
			}
		],
		failures: [],
		warnings: [],
		suggested_actions: [],
		...overrides
	};
}
