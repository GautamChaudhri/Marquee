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

type PlotLayout = {
	uirevision?: string;
	xaxis?: object;
	yaxis?: object;
	scene?: {
		uirevision?: string;
		camera?: object;
		xaxis?: object;
		yaxis?: object;
		zaxis?: object;
	};
};
type PlotConfig = { responsive?: boolean };

afterEach(() => {
	// mockReset, not mockClear: the click test installs an implementation on
	// react, which must not leak into the other tests.
	plotly.react.mockReset();
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
		// Scoped to the mode, so the 2D pan and the 3D orbit are remembered separately.
		expect(selectedLayout.uirevision).toContain('2d');
	});

	it('takes a new revision for a new coordinate space, so the plot re-frames', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		const view = render(TasteMap, {
			props: { points, dataset: 'films:2026-01-01', selected: null, onSelect: vi.fn() }
		});

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const initialLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		const initialCallCount = plotly.react.mock.calls.length;

		await view.rerender({ points, dataset: 'shows:2026-01-01', selected: null, onSelect: vi.fn() });
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(initialCallCount));
		const switchedLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;

		expect(switchedLayout.uirevision).not.toBe(initialLayout.uirevision);
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

	it('builds each 2D axis as its own object, so Plotly range restores cannot cross', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		render(TasteMap, { props: { points, selected: null, onSelect: vi.fn() } });

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const layout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;

		// Plotly writes ranges back into these objects in place; a shared
		// reference had the y-axis restore overwrite the x-axis, snapping the
		// view back after every pan.
		expect(layout.xaxis).toBeTruthy();
		expect(layout.yaxis).toBeTruthy();
		expect(layout.xaxis).not.toBe(layout.yaxis);
	});

	it('builds each 3D scene axis as its own object', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		render(TasteMap, { props: { points, mode: '3d', selected: null, onSelect: vi.fn() } });

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const scene = (plotly.react.mock.calls.at(-1)?.[2] as PlotLayout).scene;

		expect(scene?.xaxis).toBeTruthy();
		expect(scene?.yaxis).toBeTruthy();
		expect(scene?.zaxis).toBeTruthy();
		expect(scene?.xaxis).not.toBe(scene?.yaxis);
		expect(scene?.yaxis).not.toBe(scene?.zaxis);
		expect(scene?.xaxis).not.toBe(scene?.zaxis);
	});

	it('sends the default camera only when the revision changes, never on a redraw', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		const view = render(TasteMap, {
			props: { points, mode: '3d', dataset: 'films:1', selected: null, onSelect: vi.fn() }
		});

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const initialLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		expect(initialLayout.scene?.camera).toBeDefined();
		let callCount = plotly.react.mock.calls.length;

		// A selection redraw keeps the revision: no camera, so the user's orbit
		// is never even offered a default to fall back to.
		await view.rerender({
			points,
			mode: '3d',
			dataset: 'films:1',
			selected: points[0],
			onSelect: vi.fn()
		});
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(callCount));
		const selectedLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		expect(selectedLayout.scene?.camera).toBeUndefined();
		callCount = plotly.react.mock.calls.length;

		// A dataset switch is a genuine reframe: the default eye comes back.
		await view.rerender({
			points,
			mode: '3d',
			dataset: 'shows:1',
			selected: points[0],
			onSelect: vi.fn()
		});
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(callCount));
		const switchedLayout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		expect(switchedLayout.scene?.camera).toBeDefined();
	});

	it('ignores clicks on traces without customdata instead of selecting a wrong point', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		// The component binds plotly_click via the `on` emitter Plotly attaches to
		// the plot div; give the mock the same surface.
		plotly.react.mockImplementation((el: HTMLElement & { on?: unknown }) => {
			el.on ??= vi.fn();
		});
		const onSelect = vi.fn();
		render(TasteMap, { props: { points, selected: null, onSelect } });

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const graph = plotly.react.mock.calls[0]?.[0] as HTMLElement & {
			on?: ReturnType<typeof vi.fn>;
		};
		const clickCall = graph.on?.mock.calls.find(([name]) => name === 'plotly_click');
		expect(clickCall).toBeTruthy();
		const handleClick = clickCall?.[1] as (event: unknown) => void;

		// The selection ring and legend proxy traces carry no customdata; their
		// pointIndex is meaningless as a points[] index.
		handleClick({ points: [{ pointIndex: 0 }] });
		expect(onSelect).not.toHaveBeenCalled();

		handleClick({ points: [{ customdata: 0 }] });
		expect(onSelect).toHaveBeenCalledWith(points[0]);
	});

	it('keeps the trace count stable across select and deselect', async () => {
		// Adding/removing the selection ring changed the gl3d trace count, which
		// rebuilds the scene and threw the camera home — so the ring is always
		// present, just empty when nothing is selected.
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		const view = render(TasteMap, {
			props: { points, mode: '3d', selected: null, onSelect: vi.fn() }
		});

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const initialTraces = plotly.react.mock.calls.at(-1)?.[1] as unknown[];
		const initialCallCount = plotly.react.mock.calls.length;

		await view.rerender({ points, mode: '3d', selected: points[0], onSelect: vi.fn() });
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(initialCallCount));
		const selectedTraces = plotly.react.mock.calls.at(-1)?.[1] as unknown[];

		expect(selectedTraces.length).toBe(initialTraces.length);
	});

	it('re-sends the camera the user orbited to on a same-revision rebuild', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		plotly.react.mockImplementation((el: HTMLElement & { on?: unknown }) => {
			(el as { on?: unknown }).on ??= vi.fn();
		});
		const view = render(TasteMap, {
			props: { points, mode: '3d', dataset: 'films:1', selected: null, onSelect: vi.fn() }
		});

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const graph = plotly.react.mock.calls[0]?.[0] as HTMLElement & {
			on?: ReturnType<typeof vi.fn>;
		};
		const relayoutCall = graph.on?.mock.calls.find(([name]) => name === 'plotly_relayout');
		expect(relayoutCall).toBeTruthy();
		const handleRelayout = relayoutCall?.[1] as (event: unknown) => void;

		const orbited = { eye: { x: 0.4, y: -2.1, z: 0.9 } };
		handleRelayout({ 'scene.camera': orbited });
		const callCount = plotly.react.mock.calls.length;

		await view.rerender({
			points,
			mode: '3d',
			dataset: 'films:1',
			selected: points[0],
			onSelect: vi.fn()
		});
		await waitFor(() => expect(plotly.react.mock.calls.length).toBeGreaterThan(callCount));
		const layout = plotly.react.mock.calls.at(-1)?.[2] as PlotLayout;
		expect(layout.scene?.camera).toEqual(orbited);
		// A copy, not the tracked object — Plotly mutates what it is handed.
		expect(layout.scene?.camera).not.toBe(orbited);
	});

	it('opts out of Plotly responsive mode, leaving the ResizeObserver as the only driver', async () => {
		vi.stubGlobal('ResizeObserver', ResizeObserverStub);
		render(TasteMap, { props: { points, selected: null, onSelect: vi.fn() } });

		await waitFor(() => expect(plotly.react).toHaveBeenCalled());
		const config = plotly.react.mock.calls.at(-1)?.[3] as PlotConfig;
		expect(config.responsive).toBe(false);
	});
});
