<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let busy = $state(false);
</script>

<svelte:head>
	<title>Sign in · Marquee</title>
</svelte:head>

<main class="signin">
	<form
		method="POST"
		class="card mq-rise"
		use:enhance={() => {
			busy = true;
			return async ({ update }) => {
				await update();
				busy = false;
			};
		}}
	>
		<h1>Marquee</h1>
		{#if !data.configured}
			<p class="message bad" role="alert">
				No API key is configured on the web server. Set <code>MARQUEE_API_KEY</code> in its environment
				and start it again.
			</p>
		{:else}
			<p class="hint">Sign in with the API key this server was given.</p>
			<label for="key">API key</label>
			<input id="key" name="key" type="password" autocomplete="current-password" required />
			{#if form?.message}<p class="message bad" role="alert">{form.message}</p>{/if}
			<button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
		{/if}
	</form>
</main>

<style>
	.signin {
		min-height: 100vh;
		display: grid;
		place-items: center;
		padding: 16px;
	}
	.card {
		width: 100%;
		max-width: 360px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 24px;
		background: var(--panel);
		border: 1px solid var(--line2);
		border-radius: var(--radius);
		box-shadow: 0 24px 60px var(--shadow);
	}
	h1 {
		margin: 0 0 4px;
		font-size: 20px;
		font-weight: 700;
		color: var(--gold-copy);
	}
	.hint {
		margin: 0;
		color: var(--muted);
		font-size: 13px;
	}
	label {
		font-size: 12.5px;
		color: var(--muted);
	}
	input {
		padding: 9px 11px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: var(--ink2);
		color: var(--text);
		font: inherit;
	}
	button {
		margin-top: 4px;
		padding: 9px 16px;
		border-radius: var(--radius-sm);
		border: 1px solid var(--gold-deep);
		background: linear-gradient(180deg, var(--gold), var(--gold-deep));
		color: var(--on-gold);
		font-weight: 600;
	}
	button:disabled {
		opacity: 0.6;
		cursor: progress;
	}
	.message {
		margin: 0;
		font-size: 13px;
	}
	.bad {
		color: var(--bad);
	}
	code {
		font-family: var(--font-mono);
		font-size: 12px;
	}
</style>
