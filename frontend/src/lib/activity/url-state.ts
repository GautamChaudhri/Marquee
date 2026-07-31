import type { ListJobsQuery } from './types';

export type ActivityView = 'queue' | 'history' | 'operations';

export interface ActivityUrlState {
	view: ActivityView;
	q: string;
	featureArea: string;
	jobType: string;
	subjectKind: string;
	phase: string;
	outcome: string;
	attention: string;
	trigger: string;
	rootId: string;
	correlationId: string;
	workerId: string;
	executionClass: string;
	createdAfter: string;
	createdBefore: string;
	sort: string;
}

const VIEWS = new Set<ActivityView>(['queue', 'history', 'operations']);
const FEATURES = new Set([
	'ai_posters',
	'library_integrations',
	'ml_taste',
	'maintenance',
	'system'
]);
const ATTENTION = new Set(['normal', 'warning', 'error']);
const TRIGGERS = new Set([
	'manual',
	'schedule',
	'policy',
	'batch',
	'parent',
	'healing',
	'system',
	'webhook'
]);
const QUEUE_PHASES = new Set(['planned', 'queued', 'running', 'stopping']);
const HISTORY_OUTCOMES = new Set([
	'succeeded',
	'partially_succeeded',
	'no_change',
	'failed',
	'cancelled',
	'superseded',
	'unsafe',
	'dead_letter'
]);

function allowed(value: string | null, values: Set<string>): string {
	return value && values.has(value) ? value : '';
}

export function parseActivityUrl(params: URLSearchParams): ActivityUrlState {
	const rawView = params.get('view') as ActivityView | null;
	const view = rawView && VIEWS.has(rawView) ? rawView : 'queue';
	return {
		view,
		q: (params.get('q') ?? '').slice(0, 100),
		featureArea: allowed(params.get('feature_area'), FEATURES),
		jobType: (params.get('type') ?? '').slice(0, 80),
		subjectKind: (params.get('subject_kind') ?? '').slice(0, 40),
		phase: view === 'queue' ? allowed(params.get('phase'), QUEUE_PHASES) : '',
		outcome: view === 'history' ? allowed(params.get('outcome'), HISTORY_OUTCOMES) : '',
		attention: allowed(params.get('attention'), ATTENTION),
		trigger: allowed(params.get('trigger'), TRIGGERS),
		rootId: (params.get('root_id') ?? '').slice(0, 32),
		correlationId: (params.get('correlation_id') ?? '').slice(0, 64),
		workerId: (params.get('worker_id') ?? '').slice(0, 100),
		executionClass: (params.get('execution_class') ?? '').slice(0, 80),
		createdAfter: params.get('created_after') ?? '',
		createdBefore: params.get('created_before') ?? '',
		sort:
			view === 'history' && ['default', 'created', '-created'].includes(params.get('sort') ?? '')
				? (params.get('sort') ?? 'default')
				: 'default'
	};
}

function epoch(value: string): number | undefined {
	if (!value) return undefined;
	const parsed = Date.parse(value);
	return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : undefined;
}

export function activityListQuery(state: ActivityUrlState): ListJobsQuery {
	if (state.view === 'operations') return { view: 'queue', hierarchy: 'activity', limit: 1 };
	return {
		view: state.view,
		hierarchy: 'activity',
		limit: 50,
		sort: state.sort,
		q: state.q || undefined,
		feature_area: (state.featureArea || undefined) as ListJobsQuery['feature_area'],
		type: state.jobType || undefined,
		subject_kind: state.subjectKind || undefined,
		phase: state.phase || undefined,
		outcome: state.outcome || undefined,
		attention: (state.attention || undefined) as ListJobsQuery['attention'],
		trigger: (state.trigger || undefined) as ListJobsQuery['trigger'],
		root_id: state.rootId || undefined,
		correlation_id: state.correlationId || undefined,
		worker_id: state.workerId || undefined,
		execution_class: state.executionClass || undefined,
		created_after: epoch(state.createdAfter),
		created_before: epoch(state.createdBefore)
	};
}

export function activityParams(state: ActivityUrlState): URLSearchParams {
	const params = new URLSearchParams();
	params.set('view', state.view);
	const values: Array<[string, string]> = [
		['q', state.q],
		['feature_area', state.featureArea],
		['type', state.jobType],
		['subject_kind', state.subjectKind],
		['phase', state.view === 'queue' ? state.phase : ''],
		['outcome', state.view === 'history' ? state.outcome : ''],
		['attention', state.attention],
		['trigger', state.trigger],
		['root_id', state.rootId],
		['correlation_id', state.correlationId],
		['worker_id', state.workerId],
		['execution_class', state.executionClass],
		['created_after', state.createdAfter],
		['created_before', state.createdBefore],
		['sort', state.sort === 'default' ? '' : state.sort]
	];
	for (const [key, value] of values) if (value) params.set(key, value);
	return params;
}

export function activityScopeKey(state: ActivityUrlState): string {
	return `activity:${activityParams(state).toString()}`;
}
