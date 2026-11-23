import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { ArtworkCoverage as ArtworkCoverageModel } from '$lib/tv-artwork-coverage';
import ArtworkCoverage from './ArtworkCoverage.svelte';

const complete: ArtworkCoverageModel = {
	state: 'deployed',
	tone: 'good',
	label: 'Deployed',
	accessibleLabel: 'Deployed: all 4 posters present',
	presentPosterCount: 4,
	totalPosterCount: 4
};

const partial: ArtworkCoverageModel = {
	state: 'partial',
	tone: 'warn',
	label: 'Partially deployed',
	accessibleLabel: 'Partially deployed: 2 of 4 posters present',
	presentPosterCount: 2,
	totalPosterCount: 4
};

describe('ArtworkCoverage', () => {
	it('can make a pin decorative when its parent exposes the full status', () => {
		const { container } = render(ArtworkCoverage, {
			props: { coverage: complete, mode: 'pin', decorative: true }
		});

		expect(screen.queryByRole('img')).not.toBeInTheDocument();
		expect(container.querySelector('.coverage')).toHaveAttribute('aria-hidden', 'true');
		expect(container.querySelector('.marker')).toBeInTheDocument();
		expect(container.querySelector('svg')).not.toBeInTheDocument();
		expect(container.querySelector('.label')).not.toBeInTheDocument();
		expect(screen.queryByText('Deployed')).not.toBeInTheDocument();
	});

	it('shows the short aggregate status beside the dot in inline mode', () => {
		render(ArtworkCoverage, { props: { coverage: partial } });

		expect(
			screen.getByRole('img', { name: 'Partially deployed: 2 of 4 posters present' })
		).toBeVisible();
		expect(screen.getByText('Partially deployed')).toBeVisible();
	});
});
