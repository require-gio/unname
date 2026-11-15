<script lang="ts" module>
	export type InfoKind = 'name' | 'place' | 'company' | 'number' | 'other';

	/** Mapping from info kind to sidecar entity type. “OTHER” only used manually. */
	export const TYPE_FOR_KIND: Record<InfoKind, string> = {
		name: 'PERSON',
		place: 'LOCATION',
		company: 'ORGANIZATION',
		number: 'CUSTOMER_NUMBER',
		other: 'OTHER'
	};
</script>

<script lang="ts">
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import ChipSelect from '#lib/ui/ChipSelect.svelte';
	import Kbd from '#lib/ui/Kbd.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * Mini-menu shown after a selection.
	 * Options: “Mask “Lena””, info kind, Mask ⏎ / Cancel.
	 *
	 * A value is always masked everywhere it occurs – masking a single
	 * instance while leaving the same name elsewhere would be ineffective,
	 * and the PDF check would reject it on save. Therefore the menu does not
	 * offer a choice, it only reports how many places are affected.
	 *
	 * Additional actions for the same area:
	 * - `expand`: extend the selection beyond the detected place – “extend place”.
	 *   The kind stays the same.
	 * - `shorten`: shrink the selection to the detected place – “mask only …”.
	 * - `search`: Alt+M without a selection – type the string and search across
	 *   all files.
	 *
	 * Enter masks, Escape cancels; docking is handled by the viewer.
	 */
	let {
		text = $bindable(''),
		kind = $bindable('name'),
		occurrence,
		files,
		mode = 'addLater',
		notice,
		loads = false,
		pfeil = true,
		onmask,
		oncancel,
		class: className
	}: {
		text?: string;
		kind?: InfoKind;
		occurrence: number;
		files: number;
		mode?: 'addLater' | 'expand' | 'shorten' | 'search';
		/** Message below the buttons: why it’s disabled or that nothing was found. */
		notice?: string;
		/** Occurrences are still being counted. */
		loads?: boolean;
		pfeil?: boolean;
		onmask: () => void;
		oncancel: () => void;
		class?: string;
	} = $props();

	const titleId = $props.id();
	const KINDS: InfoKind[] = ['name', 'place', 'company', 'number', 'other'];
	const options = $derived(KINDS.map((value) => ({ value, label: t(`selection.kind.${value}`) })));
	const withKind = $derived(mode === 'addLater' || mode === 'search');
	// Mask each occurrence only after counting: the set of files is known after the search.
	const ready = $derived(
		mode === 'search'
			? text.trim().length >= 2 && occurrence > 0 && !loads
			: mode === 'addLater'
				? !loads
				: true
	);
	const everywhere = $derived(
		files > 1
			? t('selection.everywhere.files', { n: occurrence, files })
			: t('selection.everywhere', { n: occurrence })
	);

	const title = $derived(
		mode === 'expand'
			? t('selection.expand.title', { text })
			: mode === 'shorten'
				? t('selection.shorten.title', { text })
				: t('selection.title', { text })
	);
	const button = $derived(
		mode === 'expand'
			? t('selection.expand')
			: mode === 'shorten'
				? t('selection.shorten')
				: t('selection.mask')
	);

	function hotkey(event: KeyboardEvent): void {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			oncancel();
			return;
		}
		// Enter in the search field or on chips triggers masking; on a button it triggers its action.
		const target = event.target as HTMLElement;
		if (event.key === 'Enter' && target.tagName !== 'BUTTON' && ready) {
			event.preventDefault();
			onmask();
		}
	}
</script>

<div
	role="dialog"
	aria-labelledby={titleId}
	tabindex="-1"
	onkeydown={hotkey}
	class={cn(
		'relative flex w-80.5 flex-col gap-3 rounded-lg border border-line-strong bg-surface p-4 text-ink shadow-pop outline-none',
		className
	)}
>
	{#if pfeil}
		<span
			aria-hidden="true"
			class="absolute -top-1.75 left-13 size-3 rotate-45 border-t border-l border-line-strong bg-surface"
		></span>
	{/if}
	{#if mode === 'search'}
		<label class="flex flex-col gap-1.5">
			<span id={titleId} class="font-semibold">{t('selection.search')}</span>
			<TextInput bind:value={text} placeholder={t('selection.search.placeholder')} />
		</label>
	{:else}
		<span id={titleId} class="font-semibold">{title}</span>
	{/if}
	{#if withKind}
		<ChipSelect label={t('selection.kind')} {options} bind:value={kind} />
	{/if}
	{#if (mode === 'addLater' && occurrence > 1) || (mode === 'search' && occurrence > 0)}
		<p class="text-label text-ink-2">{everywhere}</p>
	{/if}
	<div class="flex gap-2">
		<Button variant="primary" size="sm" class="h-9.5 flex-1" disabled={!ready} onclick={onmask}>
			{button}<Kbd tone="quiet">⏎</Kbd>
		</Button>
		<Button variant="ghost" size="sm" class="h-9.5" onclick={oncancel}>
			{t('common.cancel')}
		</Button>
	</div>
	{#if notice}<p role="status" class="text-caption text-amber-ink-2">{notice}</p>{/if}
</div>
