import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from '$lib/api/types';
import IntegrationCard from './IntegrationCard.svelte';

vi.mock('$env/dynamic/public', () => ({ env: { PUBLIC_USE_MOCKS: 'false' } }));

function settings(): RuntimeSettings {
	return {
		configuration_version: 7,
		values: { RADARR_URL: 'http://radarr:7878' },
		secrets: {
			RADARR_API_KEY: {
				configured: true,
				source: 'managed',
				generation: 3,
				updated_at: null
			}
		},
		secret_store: { writable: true, reason: null }
	} as unknown as RuntimeSettings;
}

describe('IntegrationCard internal HTTP mode', () => {
	it('keeps connection actions available on a trusted HTTP network', async () => {
		render(IntegrationCard, {
			provider: 'radarr',
			settings: settings(),
			secureContext: false,
			onSettings: vi.fn()
		});

		const credential = screen.getByLabelText('API key') as HTMLInputElement;
		const test = screen.getByRole('button', { name: 'Test connection' });
		const save = screen.getByRole('button', { name: 'Test & save' });
		const clear = screen.getByRole('button', { name: 'Clear credential' });
		expect(credential).toBeEnabled();
		expect(test).toBeEnabled();
		expect(save).toBeDisabled();
		expect(clear).toBeEnabled();
		expect(screen.getByText(/Internal HTTP mode is active/)).toBeVisible();
	});
});
