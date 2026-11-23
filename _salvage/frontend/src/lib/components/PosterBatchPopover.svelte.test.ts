import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import PosterBatchPopover from './PosterBatchPopover.svelte';

const trigger = () => screen.getByTestId('batch-trigger');
const control = () => screen.getByLabelText('Poster batch processing');

describe('PosterBatchPopover', () => {
	it('reports the current setting without being opened', () => {
		render(PosterBatchPopover);

		expect(trigger()).toHaveTextContent('Batching · Chunks of 8');
		expect(control()).not.toBeVisible();
	});

	it('reveals the control on click and tracks the mode in its label', async () => {
		render(PosterBatchPopover);

		await fireEvent.click(trigger());
		expect(control()).toBeVisible();

		await fireEvent.click(screen.getByRole('button', { name: 'Unified' }));
		expect(trigger()).toHaveTextContent('Batching · Unified');
	});

	it('reflects a non-default chunk size', () => {
		render(PosterBatchPopover, { props: { chunkSize: 12 } });

		expect(trigger()).toHaveTextContent('Batching · Chunks of 12');
	});

	it('closes on Escape', async () => {
		render(PosterBatchPopover);

		await fireEvent.click(trigger());
		expect(control()).toBeVisible();

		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(control()).not.toBeVisible();
	});
});
