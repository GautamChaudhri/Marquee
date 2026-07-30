import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import PosterBatchModeControl from './PosterBatchModeControl.svelte';

describe('PosterBatchModeControl', () => {
	it('defaults to eight-item chunks and exposes all-at-once cancellation semantics', async () => {
		render(PosterBatchModeControl);

		expect(screen.getByRole('button', { name: 'Chunks' })).toHaveAttribute('aria-pressed', 'true');
		expect(screen.getByRole('spinbutton', { name: 'Chunk size' })).toHaveValue(8);
		expect(screen.getByText('Each completed chunk becomes reviewable.')).toBeVisible();

		await fireEvent.click(screen.getByRole('button', { name: 'All at once' }));

		expect(screen.queryByRole('spinbutton', { name: 'Chunk size' })).not.toBeInTheDocument();
		expect(screen.getByText('Faster startup; cancelling stops the whole batch.')).toBeVisible();
	});
});
