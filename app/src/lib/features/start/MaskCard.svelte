<script lang="ts">
	import Button from '#lib/ui/Button.svelte';
	import MaskingControl from '#lib/ui/MaskingControl.svelte';
	import Popover from '#lib/ui/Popover.svelte';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import type { MessageKey } from '#lib/i18n/translate.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { start } from '#lib/state/start.svelte.js';
	import MaskIllustration from './MaskIllustration.svelte';

	/**
	 * Card on the start page (Board "1 · New") showing what happens to
	 * "Sabine Hofer" and offering "Adjust masking" with reversible /
	 * irreversible options and "mask all". Illustration and text are
	 * configurable.
	 */
	const masking = start.masking;
	const titleId = $props.id();
	let open = $state(false);

	const kind = $derived(
		masking.all ? 'all' : masking.method === 'reversible' ? 'reversible' : 'irreversible'
	);
	const texts: Record<typeof kind, { title: MessageKey; text: MessageKey }> = {
		reversible: { title: 'start.mask.reversible.title', text: 'start.mask.reversible.text' },
		irreversible: {
			title: 'start.mask.irreversible.title',
			text: 'start.mask.irreversible.text'
		},
		all: { title: 'start.mask.all.title', text: 'start.mask.all.text' }
	};
</script>

<section
	aria-labelledby={titleId}
	class="flex flex-wrap items-center gap-11 rounded-xl border border-line bg-surface px-11 py-7.5"
>
	<MaskIllustration method={masking.method} all={masking.all} />
	<div class="flex min-w-0 flex-[1_1_300px] flex-col items-start gap-3">
		<h2 id={titleId} class="text-heading font-semibold tracking-[-0.015em]">
			{t(texts[kind].title)}
		</h2>
		<p class="max-w-110 text-lead text-pretty text-muted">{t(texts[kind].text)}</p>
		<span class="mt-1 inline-flex" {@attach coachmark(1, 'help.start.masking', { page: 'right' })}>
			<Popover bind:open label={t('masking.adjust')}>
				{#snippet trigger(props)}
					<Button {...props} size="lg" icon="regler" iconRight={open ? 'zuklappen' : 'aufklappen'}>
						{t('masking.adjust')}
					</Button>
				{/snippet}
				<MaskingControl {masking} scope={t('masking.applies.new')}>
					{#snippet reference()}
						<Button
							variant="link"
							size="sm"
							onclick={() => {
								open = false;
								settings.open('masking');
							}}
						>
							{t('common.moreSettings')}
						</Button>
					{/snippet}
				</MaskingControl>
			</Popover>
		</span>
	</div>
</section>
