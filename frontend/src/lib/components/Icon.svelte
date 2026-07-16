<script lang="ts">
	let { name, size = 18, stroke = 2 }: { name: string; size?: number; stroke?: number } = $props();

	const P: Record<string, string> = {
		dashboard:
			'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
		film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h4M3 16.5h4M17 7.5h4M17 12h4M17 16.5h4"/>',
		tv: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="m17 2-5 5-5-5"/>',
		pipeline:
			'<path d="M3 5h12M3 12h18M3 19h9"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/>',
		taste: '<path d="M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4z"/>',
		hdr: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z"/>',
		subtitles:
			'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 11h4M7 15h7M14 11h3"/>',
		letterbox: '<path d="M6 3v15a3 3 0 0 0 3 3h12"/><path d="M18 21V6a3 3 0 0 0-3-3H3"/>',
		'projection-room':
			'<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4.5-5.5"/><circle cx="12" cy="18" r="1.3"/>',
		activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
		settings:
			'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V23a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 21.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.7 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h.1A1.7 1.7 0 0 0 10 3.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1.2 2.9 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9z"/>',
		search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
		sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
		moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
		collapse: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
		chevron: '<path d="m9 6 6 6-6 6"/>',
		grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
		list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
		refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
		maximize:
			'<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M3 16v3a2 2 0 0 0 2 2h3"/>',
		x: '<path d="M18 6 6 18M6 6l12 12"/>'
	};
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width={stroke}
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -- P is a static internal icon table, no user input -->
	{@html P[name] ?? ''}
</svg>
