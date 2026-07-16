import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import Smoke from './Smoke.svelte';

describe('component test harness (client project)', () => {
	it('renders a Svelte 5 runes component into jsdom', () => {
		render(Smoke, { props: { label: 'ready' } });
		expect(screen.getByTestId('smoke')).toBeInTheDocument();
		expect(screen.getByTestId('smoke')).toHaveTextContent('ready');
	});

	it('applies the default prop', () => {
		render(Smoke);
		expect(screen.getByTestId('smoke')).toHaveTextContent('smoke');
	});
});
