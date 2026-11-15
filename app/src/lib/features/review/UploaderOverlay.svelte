<script lang="ts">
	import { t } from '#lib/state/i18n.svelte.js';
	import DropZone from '#lib/ui/DropZone.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';

	/**
	 * „+" on the file tabs: add more documents to the entry.
	 * They get the same masking; the shell reads the files. The overlay covers the
	 * document area only, not the whole app.
	 */
	let {
		open = $bindable(false),
		files,
		active = false,
		locked,
		busy,
		onselect
	}: {
		open?: boolean;
		/** Files already attached to the entry. */
		files: number;
		/** A drag operation is currently over the window. */
		active?: boolean;
		locked?: string;
		busy?: string;
		onselect: () => void;
	} = $props();

	function hotkey(event: KeyboardEvent): void {
		if (event.key !== 'Escape') return;
		event.preventDefault();
		event.stopPropagation();
		open = false;
	}
</script>

{#if open || active}
	<div
		class="absolute inset-0 z-40 flex items-center justify-center bg-overlay p-8 backdrop-blur-xs"
	>
		<div
			role="dialog"
			aria-label={t('uploader.title')}
			tabindex="-1"
			onkeydown={hotkey}
			class="relative w-[560px] max-w-full outline-none"
			{@attach (element) => {
				element.querySelector<HTMLElement>('button:not([disabled])')?.focus();
			}}
		>
			<DropZone
				variant="kompakt"
				title={t('uploader.title')}
				text={t('uploader.text', { n: files })}
				formats={t('uploader.formats')}
				{active}
				{locked}
				{busy}
				onfiles={onselect}
				class="border-2 border-accent shadow-dialog"
			/>
			<IconButton
				label={t('common.close')}
				icon="close"
				variant="quiet"
				class="absolute top-3 right-3"
				onclick={() => (open = false)}
			/>
		</div>
	</div>
{/if}
