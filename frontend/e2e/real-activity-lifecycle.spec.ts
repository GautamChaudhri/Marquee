import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('renders a real canonical PgQueuer job from the disposable FastAPI lifecycle', async ({
	page
}) => {
	await page.goto('/projection-room?view=history');
	await expect(page.getByRole('heading', { name: 'Activity', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'System no-op' })).toBeVisible({
		timeout: 30_000
	});
	await expect(page.getByText('Run a system health check', { exact: true })).toBeVisible();
});

test('keeps the real Activity lifecycle usable at phone width and free of axe violations', async ({
	page
}, testInfo) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/projection-room?view=history');
	await expect(page.getByRole('heading', { name: 'System no-op' })).toBeVisible({
		timeout: 30_000
	});
	await expect(page.getByRole('button', { name: /History/ })).toBeVisible();
	await expect(page.getByLabel('Search subjects')).toBeVisible();

	const results = await new AxeBuilder({ page }).include('main').analyze();
	await testInfo.attach('real-activity-axe-violations.json', {
		body: JSON.stringify(results.violations, null, 2),
		contentType: 'application/json'
	});
	expect(results.violations).toEqual([]);
});

test('reconciles a validated post-effect deployment after its terminal projection failed', async ({
	page
}) => {
	const response = await page.request.get('/api/onboarding/status');
	expect(response.ok()).toBeTruthy();
	const status = (await response.json()) as {
		active_positive_subjects: number;
		lineage: {
			deployment: Array<{
				job_id: string;
				state: string;
				post_effect_validation: { validated: boolean } | null;
			}>;
		};
	};
	const recovered = status.lineage.deployment.find(
		(item) => item.state === 'failed' && item.post_effect_validation?.validated
	);
	expect(recovered).toBeTruthy();
	expect(status.active_positive_subjects).toBe(49);

	await page.goto('/onboarding');
	await expect(page.getByText('49 of 50', { exact: true })).toBeVisible();
});

test('keeps unvalidated terminal no-change evidence pending during real status reconciliation', async ({
	page
}) => {
	const response = await page.request.get('/api/onboarding/status');
	expect(response.ok()).toBeTruthy();
	const status = (await response.json()) as {
		active_positive_subjects: number;
		lineage: {
			deployment: Array<{
				state: string;
				post_effect_validation: { validated: boolean } | null;
			}>;
		};
	};
	expect(
		status.lineage.deployment.some(
			(item) => item.state === 'no_change' && item.post_effect_validation === null
		)
	).toBeTruthy();
	expect(status.active_positive_subjects).toBe(49);

	await page.goto('/onboarding?review=jmc7c-unvalidated-no-change-run');
	const review = page.getByTestId('candidate-review');
	await expect(review).toContainText('JMC7C Unvalidated No-change Fixture');
	await expect(review.getByRole('button', { name: 'Choose this poster' })).toBeDisabled();
});

test('renders only verified neutral survivors from a mixed real review archive', async ({
	page
}) => {
	const response = await page.request.get('/api/onboarding/runs/jmc7c-mixed-review-run/review');
	expect(response.ok()).toBeTruthy();
	const review = (await response.json()) as {
		candidates: Array<{ candidate_id: string; eligibility: { status: string } }>;
		rejections: { archived: number; eligible: number };
	};
	expect(review.rejections).toMatchObject({ archived: 2, eligible: 1 });
	expect(review.candidates).toHaveLength(1);
	expect(review.candidates[0]?.eligibility.status).toBe('survived_objective_filters');

	await page.goto('/onboarding?review=jmc7c-mixed-review-run');
	const cards = page
		.getByRole('article')
		.filter({ has: page.getByRole('heading', { name: 'Poster choice' }) });
	await expect(cards).toHaveCount(1);
	await expect(page.getByText('rejected.jpg', { exact: true })).toHaveCount(0);
	await expect(page.getByText(/None is preselected/)).toBeVisible();
});

test('fails closed in the real review UI when retained survivor bytes are corrupt', async ({
	page
}) => {
	const response = await page.request.get('/api/onboarding/runs/jmc7c-corrupt-review-run/review');
	expect(response.status()).toBe(404);

	await page.goto('/onboarding?review=jmc7c-corrupt-review-run');
	const alert = page.getByRole('alert');
	await expect(alert).toContainText('GET /onboarding/runs/jmc7c-corrupt-review-run/review → 404');
	await expect(alert).toContainText('Refresh the page or open the analysis details');
	await expect(page.getByTestId('candidate-review')).toHaveCount(0);
});

test('submits a real onboarding choice and tracks its deployment job', async ({ page }) => {
	const activityListRequests: string[] = [];
	page.on('request', (request) => {
		if (new URL(request.url()).pathname === '/api/jobs') activityListRequests.push(request.url());
	});
	await page.goto('/onboarding?review=jmc7c-choice-run');
	const review = page.getByTestId('candidate-review');
	await expect(review).toBeVisible();
	await expect(review).toContainText('JMC7C Choice Fixture');
	await expect(page.getByText('49 of 50', { exact: true })).toBeVisible();

	const choose = review.getByRole('button', { name: 'Choose this poster' });
	await choose.focus();
	await choose.press('Enter');
	await expect(review).toContainText(
		'Your choice was recorded. Marquee is validating the selected poster deployment.'
	);
	const deployment = page
		.getByRole('article')
		.filter({ has: page.getByRole('heading', { name: 'JMC7C Choice Fixture' }) });
	await expect(deployment.getByText('Deploy selected poster artwork', { exact: true })).toBeVisible(
		{
			timeout: 30_000
		}
	);
	await expect(deployment).toContainText('Succeeded', { timeout: 30_000 });

	await page.reload();
	const recoveredReview = page.getByTestId('candidate-review');
	await expect(recoveredReview).toBeVisible();
	await expect(recoveredReview.getByRole('button', { name: 'Choose this poster' })).toBeDisabled();
	await expect(recoveredReview.getByRole('button', { name: 'I do not want this' })).toBeDisabled();
	await expect(page.getByText('50 of 50', { exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Taste profile publication' })).toBeVisible();
	await expect(page.getByText('Current build: running.', { exact: true })).toBeVisible();
	await expect(page.getByText('Current build: queued.', { exact: true })).toBeVisible();
	// The onboarding deployment has its original bounded Activity refreshes plus
	// one scoped refresh for each newly queued profile build (movies and TV).
	expect(activityListRequests).toHaveLength(9);
});

test('cancels and retries a deployment through its persisted onboarding decision lineage', async ({
	page
}) => {
	await page.goto('/onboarding?review=jmc7c-cancel-choice-run');
	const review = page.getByTestId('candidate-review');
	await expect(review).toContainText('JMC7C Cancel Fixture');
	const decisionResponse = page.waitForResponse(
		(response) =>
			new URL(response.url()).pathname === '/api/onboarding/choose' &&
			response.request().method() === 'POST'
	);
	await review.getByRole('button', { name: 'Choose this poster' }).click();
	const decision = (await (await decisionResponse).json()) as {
		deployment_job_id: string;
		exemplar_id: string;
	};
	expect(decision.deployment_job_id).toBeTruthy();

	const snapshotResponse = await page.request.get(
		`/api/jobs/${decision.deployment_job_id}/snapshot`
	);
	expect(snapshotResponse.ok()).toBeTruthy();
	const snapshot = (await snapshotResponse.json()) as { fence_token: number };
	const cancelledResponse = await page.request.post(
		`/api/jobs/${decision.deployment_job_id}/cancel`,
		{ data: { expected_fence_token: snapshot.fence_token } }
	);
	expect(cancelledResponse.ok()).toBeTruthy();
	const cancelled = (await cancelledResponse.json()) as {
		snapshot: { fence_token: number; outcome: string | null; phase: string };
	};
	expect(cancelled.snapshot.phase).toBe('stopping');

	const jobSnapshot = async (jobId: string) => {
		const response = await page.request.get(`/api/jobs/${jobId}/snapshot`);
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as {
			fence_token: number;
			outcome: string | null;
			phase: string;
		};
	};
	await expect
		.poll(async () => (await jobSnapshot(decision.deployment_job_id)).phase, { timeout: 30_000 })
		.toBe('terminal');
	const terminal = await jobSnapshot(decision.deployment_job_id);
	expect(terminal.outcome).toBe('cancelled');

	const retryResponse = await page.request.post(`/api/jobs/${decision.deployment_job_id}/retry`, {
		data: { expected_fence_token: terminal.fence_token }
	});
	expect(retryResponse.ok()).toBeTruthy();
	const retried = (await retryResponse.json()) as { replacement_job_id: string };
	expect(retried.replacement_job_id).not.toBe(decision.deployment_job_id);
	let successorTerminal: Awaited<ReturnType<typeof jobSnapshot>> | null = null;
	for (let attempt = 0; attempt < 4; attempt += 1) {
		const successorSnapshot = await jobSnapshot(retried.replacement_job_id);
		if (successorSnapshot.phase === 'terminal') {
			successorTerminal = successorSnapshot;
			break;
		}
		const successorCancel = await page.request.post(
			`/api/jobs/${retried.replacement_job_id}/cancel`,
			{
				data: { expected_fence_token: successorSnapshot.fence_token }
			}
		);
		if (!successorCancel.ok()) {
			expect(successorCancel.status()).toBe(409);
			await page.waitForTimeout(50);
			continue;
		}
		await expect
			.poll(async () => (await jobSnapshot(retried.replacement_job_id)).phase, { timeout: 30_000 })
			.toBe('terminal');
		successorTerminal = await jobSnapshot(retried.replacement_job_id);
		break;
	}
	expect(successorTerminal?.outcome).toBe('cancelled');

	const statusResponse = await page.request.get('/api/onboarding/status');
	expect(statusResponse.ok()).toBeTruthy();
	const status = (await statusResponse.json()) as {
		lineage: {
			deployment: Array<{ exemplar_id: string; job_id: string; predecessor_job_id: string | null }>;
		};
	};
	const original = status.lineage.deployment.find(
		(item) => item.job_id === decision.deployment_job_id
	);
	const successor = status.lineage.deployment.find(
		(item) => item.job_id === retried.replacement_job_id
	);
	expect(original?.exemplar_id).toBe(decision.exemplar_id);
	expect(successor).toMatchObject({
		exemplar_id: decision.exemplar_id,
		predecessor_job_id: decision.deployment_job_id
	});
});

test('records a real negative onboarding decision without creating a deployment', async ({
	page
}) => {
	await page.goto('/onboarding?review=jmc6k-review-run');
	const review = page.getByTestId('candidate-review');
	await expect(review).toBeVisible();
	await expect(review).toContainText('JMC6K Fixture');
	await expect(review).not.toContainText(/\b(rank|score|recommendation)\b/i);

	await review.getByRole('button', { name: 'I do not want this' }).click();
	await expect(review).toContainText('No poster deployment was created');

	await page.reload();
	const recoveredReview = page.getByTestId('candidate-review');
	await expect(recoveredReview).toBeVisible();
	await expect(recoveredReview.getByRole('button', { name: 'Choose this poster' })).toBeDisabled();
	await expect(recoveredReview.getByRole('button', { name: 'I do not want this' })).toBeDisabled();
	const statusResponse = await page.request.get('/api/onboarding/status');
	expect(statusResponse.ok()).toBeTruthy();
	const status = (await statusResponse.json()) as {
		active_negative_subjects: number;
		active_positive_subjects: number;
	};
	expect(status.active_negative_subjects).toBe(1);
	expect(status.active_positive_subjects).toBe(50);
});

test('starts, cancels, and retries a neutral onboarding analysis through the real status contract', async ({
	page
}) => {
	await page.goto('/onboarding');
	await expect(page.getByText('50 of 50', { exact: true })).toBeVisible();

	// The profile publication deliberately disables the page action while its
	// two canonical builds are active. Exercise the same generated browser
	// client boundary directly so the scenario remains independent of that
	// unrelated, real in-flight lifecycle.
	const response = await page.request.post('/api/onboarding/start', { data: {} });
	expect(response.ok()).toBeTruthy();
	const body = (await response.json()) as {
		analysis_job: { job_id: string; disposition: string };
		subject: { title: string };
		status: { active_positive_subjects: number };
	};
	expect(body.subject.title).toBe('JMC6K Fixture');
	expect(body.analysis_job.disposition).toBe('created');
	expect(body.status.active_positive_subjects).toBe(50);

	const snapshotResponse = await page.request.get(`/api/jobs/${body.analysis_job.job_id}/snapshot`);
	expect(snapshotResponse.ok()).toBeTruthy();
	const snapshot = (await snapshotResponse.json()) as { fence_token: number };
	const cancelledResponse = await page.request.post(
		`/api/jobs/${body.analysis_job.job_id}/cancel`,
		{
			data: { expected_fence_token: snapshot.fence_token }
		}
	);
	expect(cancelledResponse.ok()).toBeTruthy();
	const cancelled = (await cancelledResponse.json()) as {
		snapshot: { fence_token: number; outcome: string | null; phase: string };
	};
	expect(cancelled.snapshot.phase).toBe('terminal');
	expect(cancelled.snapshot.outcome).toBe('cancelled');

	const retryResponse = await page.request.post(`/api/jobs/${body.analysis_job.job_id}/retry`, {
		data: { expected_fence_token: cancelled.snapshot.fence_token }
	});
	expect(retryResponse.ok()).toBeTruthy();
	const retried = (await retryResponse.json()) as {
		original_job_id: string;
		replacement_job_id: string;
	};
	expect(retried.original_job_id).toBe(body.analysis_job.job_id);
	expect(retried.replacement_job_id).not.toBe(body.analysis_job.job_id);

	await page.goto(`/projection-room?view=queue&job=${retried.replacement_job_id}`);
	const analyses = page.getByRole('heading', { name: 'JMC6K Fixture' });
	await expect(analyses).toHaveCount(2);
	await expect(analyses.first()).toBeVisible();
});

test('activates validated no-change deployment evidence for byte-identical artwork', async ({
	page
}) => {
	await page.goto('/onboarding?review=jmc7c-no-change-run');
	const review = page.getByTestId('candidate-review');
	await expect(review).toContainText('JMC7C No-change Fixture');
	const decisionResponse = page.waitForResponse(
		(response) =>
			new URL(response.url()).pathname === '/api/onboarding/choose' &&
			response.request().method() === 'POST'
	);
	await review.getByRole('button', { name: 'Choose this poster' }).click();
	const decision = (await (await decisionResponse).json()) as { deployment_job_id: string };
	const deploymentSnapshot = async () => {
		const response = await page.request.get(`/api/jobs/${decision.deployment_job_id}/snapshot`);
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as { outcome: string | null; phase: string };
	};
	await expect
		.poll(async () => (await deploymentSnapshot()).phase, { timeout: 30_000 })
		.toBe('terminal');
	expect((await deploymentSnapshot()).outcome).toBe('no_change');

	const statusResponse = await page.request.get('/api/onboarding/status');
	expect(statusResponse.ok()).toBeTruthy();
	const status = (await statusResponse.json()) as { active_positive_subjects: number };
	expect(status.active_positive_subjects).toBe(51);
	await page.reload();
	await expect(page.getByText('51 of 50', { exact: true })).toBeVisible();
	await expect(review.getByRole('button', { name: 'Choose this poster' })).toBeDisabled();
});

test('publishes separate real movie and TV profiles only after both consumers acknowledge them', async ({
	page
}) => {
	test.setTimeout(90_000);
	const readiness = async () => {
		const response = await page.request.get('/api/onboarding/status');
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as {
			state: string;
			consumer_reloaded: boolean;
			active_negative_subjects: number;
			libraries: Record<
				'movies' | 'tv',
				{
					active: { generation: number | null; compatible: boolean };
					reload_state: { ready: boolean };
				}
			>;
		};
	};
	await expect
		.poll(
			async () => {
				const status = await readiness();
				return [
					status.libraries.movies.active.generation,
					status.libraries.tv.active.generation
				].join(':');
			},
			{ timeout: 60_000 }
		)
		.toBe('2:2');
	const movieConsumer = await page.request.post('/api/pipeline/movie/7002/run');
	expect(movieConsumer.status()).toBe(202);
	const tvConsumer = await page.request.post('/api/pipeline/tv/series/7001/run', {
		data: { include: 'show' }
	});
	expect(tvConsumer.status()).toBe(202);
	await expect
		.poll(
			async () => {
				const status = await readiness();
				return [
					status.state,
					status.consumer_reloaded,
					status.active_negative_subjects,
					status.libraries.movies.active.generation,
					status.libraries.movies.active.compatible,
					status.libraries.movies.reload_state.ready,
					status.libraries.tv.active.generation,
					status.libraries.tv.active.compatible,
					status.libraries.tv.reload_state.ready
				].join(':');
			},
			{ timeout: 60_000 }
		)
		.toBe('personalized:true:1:2:true:true:2:true:true');

	await page.goto('/onboarding');
	await expect(page.getByText('Your taste profiles are active.', { exact: true })).toBeVisible();
	const movies = page
		.getByRole('article')
		.filter({ has: page.getByRole('heading', { name: 'Movie profile' }) });
	const television = page
		.getByRole('article')
		.filter({ has: page.getByRole('heading', { name: 'TV profile' }) });
	await expect(movies).toContainText('Active generation 2; consumer reload confirmed.');
	await expect(television).toContainText('Active generation 2; consumer reload confirmed.');
});

test('publishes generation three only after real movie and TV consumers reload it', async ({
	page
}) => {
	test.setTimeout(90_000);
	const readiness = async () => {
		const response = await page.request.get('/api/onboarding/status');
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as {
			state: string;
			consumer_reloaded: boolean;
			libraries: Record<
				'movies' | 'tv',
				{
					active: { generation: number | null; compatible: boolean };
					reload_state: { ready: boolean };
				}
			>;
		};
	};
	for (const library of ['movies', 'tv']) {
		const publication = await page.request.post('/api/taste/retrain', { data: { library } });
		expect(publication.status()).toBe(202);
	}
	await expect
		.poll(
			async () => {
				const status = await readiness();
				return [
					status.libraries.movies.active.generation,
					status.libraries.tv.active.generation
				].join(':');
			},
			{ timeout: 60_000 }
		)
		.toBe('3:3');
	const movieConsumer = await page.request.post('/api/pipeline/movie/7002/run');
	expect(movieConsumer.status()).toBe(202);
	const tvConsumer = await page.request.post('/api/pipeline/tv/series/7001/run', {
		data: { include: 'show' }
	});
	expect(tvConsumer.status()).toBe(202);
	await expect
		.poll(
			async () => {
				const status = await readiness();
				return [
					status.state,
					status.consumer_reloaded,
					status.libraries.movies.active.generation,
					status.libraries.movies.active.compatible,
					status.libraries.movies.reload_state.ready,
					status.libraries.tv.active.generation,
					status.libraries.tv.active.compatible,
					status.libraries.tv.reload_state.ready
				].join(':');
			},
			{ timeout: 60_000 }
		)
		.toBe('personalized:true:3:true:true:3:true:true');
});

test('recovers a failed TV-only profile build without invalidating the active movie profile', async ({
	page
}) => {
	test.setTimeout(90_000);
	const submissionResponse = await page.request.post('/api/taste/retrain', {
		data: { library: 'tv' }
	});
	expect(submissionResponse.status()).toBe(202);
	const submission = (await submissionResponse.json()) as { job_id: string };
	const snapshot = async (jobId: string) => {
		const response = await page.request.get(`/api/jobs/${jobId}/snapshot`);
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as {
			fence_token: number;
			outcome: string | null;
			phase: string;
		};
	};
	let cancellationAccepted = false;
	for (let attempt = 0; attempt < 6; attempt += 1) {
		const current = await snapshot(submission.job_id);
		if (current.phase === 'terminal') break;
		const cancellation = await page.request.post(`/api/jobs/${submission.job_id}/cancel`, {
			data: { expected_fence_token: current.fence_token }
		});
		if (cancellation.ok()) {
			cancellationAccepted = true;
			break;
		}
		expect(cancellation.status()).toBe(409);
		await page.waitForTimeout(50);
	}
	expect(cancellationAccepted).toBeTruthy();
	await expect
		.poll(async () => (await snapshot(submission.job_id)).phase, { timeout: 30_000 })
		.toBe('terminal');
	expect((await snapshot(submission.job_id)).outcome).toBe('cancelled');

	const partialStatus = await page.request.get('/api/onboarding/status');
	expect(partialStatus.ok()).toBeTruthy();
	expect(await partialStatus.json()).toMatchObject({
		libraries: {
			movies: { active: { generation: 3, compatible: true } },
			tv: { active: { generation: 3, compatible: true } }
		}
	});
	const retryResponse = await page.request.post(`/api/jobs/${submission.job_id}/retry`, {
		data: { expected_fence_token: (await snapshot(submission.job_id)).fence_token }
	});
	expect(retryResponse.ok()).toBeTruthy();
	const retry = (await retryResponse.json()) as { replacement_job_id: string };
	await expect
		.poll(async () => (await snapshot(retry.replacement_job_id)).phase, {
			timeout: 60_000
		})
		.toBe('terminal');
	expect((await snapshot(retry.replacement_job_id)).outcome).toBe('succeeded');

	const tvConsumer = await page.request.post('/api/pipeline/tv/series/7001/run', {
		data: { include: 'show' }
	});
	expect(tvConsumer.status()).toBe(202);
	await expect
		.poll(
			async () => {
				const response = await page.request.get('/api/onboarding/status');
				expect(response.ok()).toBeTruthy();
				const status = (await response.json()) as {
					state: string;
					libraries: Record<
						'movies' | 'tv',
						{ active: { generation: number | null; compatible: boolean } }
					>;
				};
				return [
					status.state,
					status.libraries.movies.active.generation,
					status.libraries.movies.active.compatible,
					status.libraries.tv.active.generation,
					status.libraries.tv.active.compatible
				].join(':');
			},
			{ timeout: 60_000 }
		)
		.toBe('personalized:3:true:4:true');
});

test('records a real terminal analysis failure on the retried successor lineage', async ({
	page
}) => {
	const response = await page.request.get('/api/onboarding/status');
	expect(response.ok()).toBeTruthy();
	const status = (await response.json()) as {
		lineage: {
			analysis: Array<{
				job_id: string;
				predecessor_job_id: string | null;
				phase: string;
				outcome: string | null;
			}>;
		};
	};
	const failedRetry = status.lineage.analysis.find(
		(item) => item.phase === 'terminal' && item.outcome === 'failed' && item.predecessor_job_id
	);
	expect(failedRetry).toBeTruthy();
	expect(failedRetry?.job_id).not.toBe(failedRetry?.predecessor_job_id);
});

test('submits a real residual evaluation against frozen evidence and preserves the active profile on no-change', async ({
	page
}) => {
	const submissionResponse = await page.request.post('/api/taste/residual/retrain?library=tv');
	expect(submissionResponse.status()).toBe(202);
	const submission = (await submissionResponse.json()) as { job_id: string; disposition: string };
	expect(submission.disposition).toBe('created');

	const snapshot = async () => {
		const response = await page.request.get(`/api/jobs/${submission.job_id}/snapshot`);
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as { phase: string; outcome: string | null };
	};
	await expect.poll(async () => (await snapshot()).phase, { timeout: 30_000 }).toBe('terminal');
	expect((await snapshot()).outcome).toBe('no_change');

	const statusResponse = await page.request.get('/api/onboarding/status');
	expect(statusResponse.ok()).toBeTruthy();
	const status = (await statusResponse.json()) as {
		libraries: { movies: { active: { compatible: boolean }; residual: { active: boolean } } };
	};
	expect(status.libraries.movies.active.compatible).toBeTruthy();
	expect(status.libraries.movies.residual.active).toBeFalsy();
});

test('filters a real unrelated global SSE lifecycle event without allocating a snapshot repair', async ({
	page
}) => {
	test.setTimeout(90_000);
	const repairedSnapshots: string[] = [];
	page.on('request', (request) => {
		if (/^\/api\/jobs\/[a-f0-9]{32}\/snapshot$/.test(new URL(request.url()).pathname)) {
			repairedSnapshots.push(new URL(request.url()).pathname);
		}
	});
	const streamConnected = page.waitForRequest(
		(request) => new URL(request.url()).pathname === '/api/jobs/events/stream'
	);
	await page.goto('/projection-room?view=queue&feature_area=ai_posters');
	await streamConnected;
	await page.evaluate(() => {
		const target = window as typeof window & {
			jmc7cFilterProbe?: { source: EventSource; opened: number; jobIds: string[] };
		};
		const source = new EventSource('/api/jobs/events/stream');
		const probe = { source, opened: 0, jobIds: [] as string[] };
		target.jmc7cFilterProbe = probe;
		source.addEventListener('open', () => {
			probe.opened += 1;
		});
		for (const eventName of ['job.queued', 'attempt.started', 'job.no_change', 'job.succeeded']) {
			source.addEventListener(eventName, (event) => {
				const payload = JSON.parse((event as MessageEvent<string>).data) as {
					job_id?: string | null;
				};
				if (payload.job_id) probe.jobIds.push(payload.job_id);
			});
		}
	});
	await expect
		.poll(
			() =>
				page.evaluate(
					() =>
						(
							window as typeof window & {
								jmc7cFilterProbe?: { source: EventSource; opened: number; jobIds: string[] };
							}
						).jmc7cFilterProbe?.opened ?? 0
				),
			{ timeout: 30_000 }
		)
		.toBeGreaterThan(0);

	const submissionResponse = await page.request.post('/api/taste/residual/retrain?library=movies');
	expect(submissionResponse.status()).toBe(202);
	const submission = (await submissionResponse.json()) as { job_id: string };

	const snapshot = async () => {
		const response = await page.request.get(`/api/jobs/${submission.job_id}/snapshot`);
		expect(response.ok()).toBeTruthy();
		return (await response.json()) as { phase: string };
	};
	await expect.poll(async () => (await snapshot()).phase, { timeout: 30_000 }).toBe('terminal');
	await expect
		.poll(
			() =>
				page.evaluate(
					(jobId) =>
						(
							window as typeof window & {
								jmc7cFilterProbe?: { source: EventSource; opened: number; jobIds: string[] };
							}
						).jmc7cFilterProbe?.jobIds.includes(jobId) ?? false,
					submission.job_id
				),
			{ timeout: 30_000 }
		)
		.toBeTruthy();
	await page.waitForTimeout(500);
	expect(repairedSnapshots).not.toContain(`/api/jobs/${submission.job_id}/snapshot`);
	await page.evaluate(() =>
		(
			window as typeof window & {
				jmc7cFilterProbe?: { source: EventSource; opened: number; jobIds: string[] };
			}
		).jmc7cFilterProbe?.source.close()
	);
});

test('keeps a real tracked analysis visible through browser network loss and EventSource recovery', async ({
	page
}) => {
	test.setTimeout(90_000);
	const streamRequests: string[] = [];
	page.on('request', (request) => {
		if (new URL(request.url()).pathname === '/api/jobs/events/stream') {
			streamRequests.push(request.url());
		}
	});
	await page.goto('/projection-room?view=queue');
	const analyses = page.getByRole('heading', { name: 'JMC6K Fixture' });
	await expect(analyses.first()).toBeVisible({ timeout: 30_000 });

	await page.context().setOffline(true);
	await page.evaluate(() => {
		const target = window as typeof window & {
			jmc7cTransportProbe?: { errors: number; opened: number; source: EventSource };
		};
		const source = new EventSource('/api/jobs/events/stream?after=0');
		target.jmc7cTransportProbe = { errors: 0, opened: 0, source };
		source.addEventListener('error', () => {
			target.jmc7cTransportProbe!.errors += 1;
		});
		source.addEventListener('open', () => {
			target.jmc7cTransportProbe!.opened += 1;
		});
	});
	await expect
		.poll(
			() =>
				page.evaluate(
					() =>
						(
							window as typeof window & {
								jmc7cTransportProbe?: { errors: number; opened: number; source: EventSource };
							}
						).jmc7cTransportProbe?.errors ?? 0
				),
			{ timeout: 30_000 }
		)
		.toBeGreaterThan(0);
	await expect(analyses.first()).toBeVisible();

	await page.context().setOffline(false);
	await expect
		.poll(
			() =>
				page.evaluate(
					() =>
						(
							window as typeof window & {
								jmc7cTransportProbe?: { errors: number; opened: number; source: EventSource };
							}
						).jmc7cTransportProbe?.opened ?? 0
				),
			{ timeout: 30_000 }
		)
		.toBeGreaterThan(0);
	await expect(analyses.first()).toBeVisible();
	expect(streamRequests.length).toBeGreaterThanOrEqual(3);
	await page.evaluate(() =>
		(
			window as typeof window & {
				jmc7cTransportProbe?: { errors: number; opened: number; source: EventSource };
			}
		).jmc7cTransportProbe?.source.close()
	);
});
