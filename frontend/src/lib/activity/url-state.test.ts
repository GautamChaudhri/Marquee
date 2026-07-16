import { describe, expect, it } from 'vitest';
import { activityListQuery, activityParams, parseActivityUrl } from './url-state';

describe('Activity URL state', () => {
	it('round-trips supported Queue filters without stale History state', () => {
		const state = parseActivityUrl(
			new URLSearchParams(
				'view=queue&q=Dune&feature_area=ai_posters&phase=running&outcome=failed&root_id=root-1&worker_id=node-a&execution_class=gpu'
			)
		);
		expect(state.outcome).toBe('');
		expect(activityParams(state).toString()).toContain('phase=running');
		expect(activityListQuery(state)).toMatchObject({
			view: 'queue',
			q: 'Dune',
			feature_area: 'ai_posters',
			phase: 'running',
			root_id: 'root-1',
			worker_id: 'node-a',
			execution_class: 'gpu'
		});
	});

	it('rejects unknown filters and keeps History ordering server-owned', () => {
		const state = parseActivityUrl(
			new URLSearchParams('view=history&attention=purple&outcome=no_change&sort=-created')
		);
		expect(state.attention).toBe('');
		expect(activityListQuery(state)).toMatchObject({
			view: 'history',
			outcome: 'no_change',
			sort: '-created'
		});
	});

	it('selects Operations without constructing a diagnostic fan-out', () => {
		const state = parseActivityUrl(new URLSearchParams('view=operations&q=ignored'));
		expect(state.view).toBe('operations');
		expect(activityListQuery(state)).toEqual({ view: 'queue', limit: 1 });
	});
});
