import { render, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TasteMapPoint } from '$lib/api/types';
import TasteMap from './TasteMap.svelte';

const { plotly } = vi.hoisted(() => ({
	plotly: {
		react: vi.fn(),
		Plots: { resize: vi.fn() },
		purge: vi.fn()
	}
}));

vi.mock('plotly.js-dist-min', () => ({ default: plotly }));

class ResizeObserverStub {
	constructor(callback: ResizeObserverCallback) {
		void callback;
	}

	observe() {}

	disconnect() {}
}

const points: TasteMapPoint[] = [
	{
		name: 'arrival.jpg',
		movie_title: 'Arrival',
		movie_id: 12,
		tmdb_id: 329865,
		asset_kind: 'movie',
		season_number: null,
		series_id: null,
		season_id: null,
		poster_url: '/api/library/movies/12/poster',
		x: 1,
		y: 2,
		z: 3,
		x2: 0.25,
		y2: -0.5,
		cluster: 0,
		is_noise: false,
		self_knn: 0.72,
		genres: ['Science Fiction'],
		year: 2016,
		aesthetic: 6.4,
		colorfulness: 88
	}
];

type PlotLayout = { uirevision?: string; scene?: { uirevision?: string } };

afterEach(() => {
	plotly.react.mockClear();
	plotly.Plots.resize.mockClear();
	plotly.purge.mockClear();
	vi.unstubAllGlobals();
});

describe('TasteMap', () => {
	it('keeps one Plotly UI revision when a selected point redraws the plot', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		const view = render(TasteMap, {
			props: { points, selected: null, onSelect: vi.fn() }
		});

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const initialLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		const initialCallCount = plotly.react.mock.calls.length;

		await view.rerender({ points, selected: points[0], onSelect: vi.fn() });
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(initialCallCount));
		const selectedLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;

		expect(selectedLayout.uirevision).toBe(initialLayout.uirevision);
		expect(selectedLayout.uirevision).toContain('2d:');
	});

	it('sets the scene revision too, so selecting a point does not reset a 3D orbit', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		const view = render(TasteMap, {
			props: { points, mode: '3d', selected: null, onSelect: vi.fn() }
		});

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const initialLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		const initialCallCount = plotly.react.mock.calls.length;

		await view.rerender({ points, mode: '3d', selected: points[0], onSelect: vi.fn() });
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(initialCallCount));
		const selectedLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;

		expect(selectedLayout.scene?.uirevision).toBe(initialLayout.scene?.uirevision);
		expect(selectedLayout.scene?.uirevision).toBe(selectedLayout.uirevision);
	});
});
