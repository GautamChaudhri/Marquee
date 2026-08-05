import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from '$lib/api/types';
import ConnectionRegistry from './ConnectionRegistry.svelte';

const api = vi.hoisted(() => ({
	clearIntegrationCredential: vi.fn(),
	getSettings: vi.fn(),
	putIntegration: vi.fn(),
	testIntegration: vi.fn()
}));

vi.mock('$lib/api/system', () => api);
vi.mock('$lib/api/library', () => ({ syncLibraries: vi.fn() }));
vi.mock('$lib/toast', () => ({ toast: vi.fn() }));

function settings(): RuntimeSettings {
	return {
		configuration_version: 7,
		values: { RADARR_URL: 'http://radarr:7878', SONARR_URL: '' },
		secrets: {
			TMDB_READ_ACCESS_TOKEN: {
				configured: false,
				source: 'default',
				generation: 0,
				updated_at: null
			},
			RADARR_API_KEY: {
				configured: true,
				source: 'environment',
				generation: 0,
				updated_at: null
			},
			SONARR_API_KEY: { configured: false, source: 'default', generation: 0, updated_at: null }
		},
		secret_store: { writable: false, reason: 'settings encryption keyring is not configured' },
		integrations: {
			tmdb: { configured: false, name: 'The Movie Database' },
			radarr: {
				configured: true,
				name: 'Radarr',
				url_configured: true,
				api_key_configured: true,
				path_mapping_configured: false
			},
			sonarr: {
				configured: false,
				name: 'Sonarr',
				url_configured: false,
				api_key_configured: false,
				path_mapping_configured: false
			}
		},
		sync: { interval_minutes: 15 }
	} as unknown as RuntimeSettings;
}

afterEach(() => {
	vi.clearAllMocks();
});

describe('ConnectionRegistry internal HTTP mode', () => {
	it('tests environment credentials and saves non-secret connection details over HTTP', async () => {
		api.testIntegration.mockResolvedValue({
			ok: true,
			provider: 'radarr',
			status: { appName: 'Radarr' }
		});
		api.putIntegration.mockResolvedValue({ settings: settings() });

		render(ConnectionRegistry, {
			settings: settings(),
			secureContext: false,
			onSettings: vi.fn()
		});

		const registry = screen.getByRole('region', { name: 'Connected Services' });
		await fireEvent.click(within(registry).getByRole('button', { name: /^Edit$/ }));

		const dialog = screen.getByRole('dialog', { name: 'Edit Radarr' });
		const credential = screen.getByLabelText('API key') as HTMLInputElement;
		const test = screen.getByRole('button', { name: 'Test connection' });
		const save = screen.getByRole('button', { name: 'Test & save' });

		expect(credential).toBeEnabled();
		expect(test).toBeEnabled();
		expect(save).toBeDisabled();
		expect(dialog).toHaveTextContent('Internal HTTP mode is active');
		expect(dialog).toHaveTextContent('can be tested but cannot be saved here');

		await fireEvent.click(test);
		await waitFor(() =>
			expect(api.testIntegration).toHaveBeenCalledWith(expect.any(Function), 'radarr', {
				url: 'http://radarr:7878'
			})
		);

		await fireEvent.input(credential, { target: { value: 'candidate-key' } });
		expect(save).toBeDisabled();
		await fireEvent.click(test);
		await waitFor(() =>
			expect(api.testIntegration).toHaveBeenCalledWith(expect.any(Function), 'radarr', {
				url: 'http://radarr:7878',
				credential: 'candidate-key'
			})
		);
		expect(credential).toHaveValue('');

		await fireEvent.input(screen.getByLabelText('Instance name'), {
			target: { value: 'Cinema Rack' }
		});
		expect(save).toBeEnabled();
		await fireEvent.click(save);
		await waitFor(() =>
			expect(api.putIntegration).toHaveBeenCalledWith(
				expect.any(Function),
				'radarr',
				expect.objectContaining({
					expected_version: 7,
					expected_secret_generation: 0,
					name: 'Cinema Rack'
				})
			)
		);
	});
});
