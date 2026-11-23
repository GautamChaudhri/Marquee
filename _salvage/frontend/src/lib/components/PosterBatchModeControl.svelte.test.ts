import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import PosterBatchModeControl from './PosterBatchModeControl.svelte';

describe('PosterBatchModeControl', () => {
	it('shows the chunked and unified trade-offs and has accessible custom steppers', async () => {
		render(PosterBatchModeControl);

		const modeSwitch = screen.getByRole('group', { name: 'Poster batch mode' });
		expect(
			within(modeSwitch)
				.getAllByRole('button')
				.map((button) => button.textContent)
		).toEqual(['Unified', 'Chunks']);
		expect(screen.getByRole('button', { name: 'Chunks' })).toHaveAttribute('aria-pressed', 'true');
		expect(screen.getByRole('spinbutton', { name: 'Chunk size' })).toHaveValue(8);
		expect(screen.getByText('Partially cancel individual chunks.')).toBeVisible();
		expect(screen.getByText('Slightly longer processing times.')).toBeVisible();

		await fireEvent.click(screen.getByRole('button', { name: 'Increase chunk size' }));
		expect(screen.getByRole('spinbutton', { name: 'Chunk size' })).toHaveValue(9);
		await fireEvent.click(screen.getByRole('button', { name: 'Decrease chunk size' }));
		expect(screen.getByRole('spinbutton', { name: 'Chunk size' })).toHaveValue(8);

		await fireEvent.click(screen.getByRole('button', { name: 'Unified' }));

		expect(screen.queryByRole('spinbutton', { name: 'Chunk size' })).not.toBeInTheDocument();
		expect(screen.getByText('Fastest overall processing time.')).toBeVisible();
		expect(screen.getByText('Cancelling stops everything.')).toBeVisible();
	});
});
