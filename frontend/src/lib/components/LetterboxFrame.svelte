<script lang="ts">
	interface Props {
		src: string | null;
		alt: string;
		tone?: 'before' | 'after';
		label?: string;
		placeholder?: string;
	}

	let { src, alt, tone = 'before', label, placeholder = 'No preview available' }: Props = $props();
</script>

<div class="frame-wrap">
	{#if label}
		<div class="ptitle">{label}</div>
	{/if}
	{#if src}
		<img class="frame" class:good={tone === 'after'} {src} {alt} loading="lazy" />
	{:else}
		<div class="frame unanalyzed"><span class="ph">{placeholder}</span></div>
	{/if}
</div>

<style>
	.frame-wrap {
		display: flex;
		flex-direction: column;
		gap: 8px;
		min-width: 0;
	}
	.ptitle {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--faint);
		font-weight: 700;
	}
	.frame {
		width: 100%;
		aspect-ratio: 16 / 9;
		object-fit: cover;
		border-radius: var(--radius-sm);
		border: 1px solid var(--line2);
		background: linear-gradient(150deg, #1a1410, #0a0806);
	}
	.frame.good {
		border-color: color-mix(in srgb, var(--good) 50%, var(--line2));
		/* After-crop image has a wider AR than 16:9 — let its natural height show */
		aspect-ratio: auto;
		height: auto;
		object-fit: initial;
	}
	.frame.unanalyzed {
		display: flex;
		align-items: center;
		justify-content: center;
		border-style: dashed;
		background: repeating-linear-gradient(
			45deg,
			var(--ink2),
			var(--ink2) 10px,
			var(--panel) 10px,
			var(--panel) 20px
		);
	}
	.ph {
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--gold);
	}
</style>
