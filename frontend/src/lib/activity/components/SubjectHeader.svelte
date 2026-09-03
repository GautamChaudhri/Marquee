<script lang="ts">
	import type { PresentationSubject } from '../types';

	let {
		subject,
		artworkUrl = null,
		compact = false
	}: { subject: PresentationSubject; artworkUrl?: string | null; compact?: boolean } = $props();

	// A poster chunk's name initials to a useless "T"/"M", so the server sends its own
	// lettering ("TVP"/"FP"). Everything else keeps the first-character fallback.
	const fallback = $derived(
		subject.monogram ?? (subject.display_name.trim().charAt(0).toLocaleUpperCase() || '•')
	);
	const wide = $derived((subject.monogram?.length ?? 1) > 2);
</script>

<header class:compact class="subject-header">
	<div class="artwork" class:wide aria-hidden="true">
		{#if artworkUrl}
			<img src={artworkUrl} alt="" loading="lazy" decoding="async" />
		{:else}
			<span>{fallback}</span>
		{/if}
	</div>
	<div class="identity">
		<h3>{subject.display_name}</h3>
		{#if subject.context.length > 0}
			<p class="context">{subject.context.join(' · ')}</p>
		{/if}
		{#if subject.missing_live_subject}
			<p class="missing">Saved activity · source item no longer exists</p>
		{/if}
	</div>
</header>

<style>
	.subject-header {
		display: flex;
		align-items: center;
		gap: 12px;
		min-width: 0;
	}
	.artwork {
		display: grid;
		width: 44px;
		height: 58px;
		flex: none;
		place-items: center;
		overflow: hidden;
		border: 1px solid var(--line2);
		border-radius: var(--radius-sm);
		background: linear-gradient(145deg, var(--panel2), var(--ink2));
		color: var(--gold);
		font-size: 17px;
		font-weight: 750;
	}
	.artwork img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	/* Three letters have to fit the same tile a single letter had to itself. */
	.artwork.wide {
		font-size: 13px;
		letter-spacing: -0.02em;
	}
	.compact .artwork {
		width: 34px;
		height: 44px;
		font-size: 14px;
	}
	.compact .artwork.wide {
		font-size: 11px;
	}
	.identity {
		min-width: 0;
	}
	h3,
	p {
		margin: 0;
	}
	h3 {
		overflow: hidden;
		font-size: 15px;
		line-height: 1.25;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.context,
	.missing {
		margin-top: 3px;
		color: var(--muted);
		font-size: 12px;
	}
	.context {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.missing {
		color: var(--warn);
	}
</style>
