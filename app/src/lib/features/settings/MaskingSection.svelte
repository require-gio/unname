<script lang="ts">
	import { untrack } from 'svelte';
	import MaskingControl from '#lib/ui/MaskingControl.svelte';
	import SegmentText from '#lib/ui/SegmentText.svelte';
	import { EXAMPLE_ORIGINAL, exampleSegments } from '#lib/settings/dialog.js';
	import { samePreset } from '#lib/masking/rules.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { Masking } from '#lib/state/masking.svelte.js';
	import { start } from '#lib/state/start.svelte.js';
	import { change } from './change.js';

	/**
	 * Settings › Masking: default for all new items (Board
	 * “Settings”, section “Masking”). Same `MaskingControl` as on the
	 * home page, shown here as cards with full options; below the
	 * example sentence that follows the selection.
	 *
	 * This default is also the home-page masking (“applies to all new”);
	 * both stay in sync.
	 */
	const standard = new Masking();
	let loaded = false;

	// Load saved default and re-apply if a template or the home page changed it.
	// Only a saved page triggers this; a choice here waits for its own save.
	$effect(() => {
		const saved = settings.values?.masking;
		if (!saved) return;
		untrack(() => {
			if (!loaded || !samePreset(standard.asPreset(), saved)) {
				standard.accept(saved);
			}
			loaded = true;
		});
	});

	$effect(() => {
		const preset = standard.asPreset();
		const saved = settings.values?.masking;
		if (!loaded || !saved || samePreset(preset, saved)) return;
		const timer = setTimeout(() => {
			start.masking.accept(preset);
			void change({ masking: preset });
		}, 300);
		return () => clearTimeout(timer);
	});

	const example = $derived(
		exampleSegments(
			{ method: standard.method, style: standard.style, categories: standard.categories },
			i18n.language
		)
	);
</script>

<div class="flex max-w-190 flex-col gap-6">
	<MaskingControl masking={standard} variant="cards" />
	<div class="flex flex-col gap-2.5 rounded-lg border border-line bg-surface-3 px-5 py-4">
		<span class="text-fine font-semibold tracking-[0.04em] text-muted uppercase"
			>{t('settings.masking.example')}</span
		>
		<p class="font-doc text-doc text-subtle">{EXAMPLE_ORIGINAL}</p>
		<p class="font-doc text-doc text-doc-ink" aria-live="polite">
			<SegmentText segments={example} mark={false} />
		</p>
	</div>
</div>
