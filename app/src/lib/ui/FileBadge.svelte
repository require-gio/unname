<script lang="ts">
	import { cn } from '#lib/utils.js';
	import { fileKind, type FileKind } from './file.js';

	/**
	 * Small badge indicating the file type: tabs (`small`), lists (`mittel`) and the detection list (`large`).
	 * Decorative – the file name is always shown next to it.
	 */
	let {
		name,
		kind,
		size = 'mittel',
		class: className
	}: {
		/** File name; determines type and short code. */
		name: string;
		/** Override the inferred type, e.g. “Text from image” for a scan. */
		kind?: FileKind;
		size?: 'small' | 'mittel' | 'large';
		class?: string;
	} = $props();

	const detected = $derived(fileKind(name));

	// Font size is below the design scale, so the badge is decorative,
	// the name sits beside it, and the mockup shows it at about 9–10.5 px.
	const COLOR: Record<FileKind, string> = {
		pdf: 'bg-red-1 text-red-ink',
		doc: 'bg-blue-1 text-blue-ink',
		table: 'bg-green-1 text-green-ink',
		image: 'bg-green-1 text-green-ink',
		text: 'bg-chip text-muted'
	};
</script>

<span
	aria-hidden="true"
	class={cn(
		'inline-flex shrink-0 items-center justify-center font-sans font-bold tracking-wide',
		size === 'small' && 'rounded-xs px-1 py-0.5 text-[9px] leading-none',
		size === 'mittel' && 'h-9.5 w-8 rounded-xs text-[9px]',
		size === 'large' && 'h-12 w-10 rounded-sm text-[10.5px]',
		COLOR[kind ?? detected.kind],
		className
	)}>{detected.shortCode}</span
>
