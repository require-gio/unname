<script lang="ts">
	import { cn } from '#lib/utils.js';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import { settings } from '#lib/state/settings.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * A scan or photo failed because OCR is missing. Explain what to do:
	 * load, review and view the state via Settings › Models & Storage – also shows
	 * if the program itself is missing.
	 */
	let { class: className }: { class?: string } = $props();
</script>

<div
	role="status"
	class={cn(
		'flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-line bg-amber-1 px-4 py-3',
		className
	)}
>
	<p class="flex min-w-0 flex-[1_1_320px] items-start gap-2 text-label text-amber-ink">
		<Icon name="warning" size={16} class="mt-0.5" />
		{t('ocr.notice')}
	</p>
	<Button variant="secondary" size="sm" onclick={() => settings.open('models')}>
		{t('ocr.load')}
	</Button>
</div>
