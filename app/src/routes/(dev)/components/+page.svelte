<script lang="ts">
	import { setMode, userPrefersMode } from 'mode-watcher';
	import { t } from '#lib/state/i18n.svelte.js';
	import HelpButton from '#lib/ui/HelpButton.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import BoardComponents from './BoardComponents.svelte';
	import BasicComponents from './BasicComponents.svelte';
	import Composite from './Composite.svelte';

	/**
	 * Component board and all variants, light and dark.
	 * Only reachable in a development build; `tests/smoke/components.spec.ts`
	 * holds the reference images.
	 */
	let mode = $state<'light' | 'dark' | 'system'>(userPrefersMode.current ?? 'system');
</script>

<svelte:head>
	<title>Bausteine · unname</title>
</svelte:head>

<main id="components" class="flex min-w-[1440px] flex-col bg-bg">
	<BoardComponents />

	<div data-testid="variants" class="flex flex-col gap-12 bg-bg px-12 pt-4 pb-16">
		<header class="flex items-center justify-between gap-6 border-t border-line pt-10">
			<div class="flex flex-col gap-1">
				<h2 class="font-serif text-display-lg leading-none">Alle Varianten</h2>
				<p class="text-muted">
					Grund- und zusammengesetzte Bausteine aus <code class="font-mono text-label">#lib/ui</code
					>
					mit Tokens aus <code class="font-mono text-label">tokens.css</code>.
				</p>
			</div>
			<div class="flex items-center gap-3">
				<Segmented
					label={t('settingsDialog.appearance')}
					bind:value={mode}
					onwechsel={(fresh) => setMode(fresh)}
					options={[
						{ value: 'light', label: t('settingsDialog.appearance.light') },
						{ value: 'dark', label: t('settingsDialog.appearance.dark') },
						{ value: 'system', label: t('settingsDialog.appearance.system') }
					]}
				/>
				<HelpButton />
			</div>
		</header>

		<section aria-label="Grundbausteine" class="flex flex-col gap-6">
			<h2 class="font-serif text-[32px] leading-none">Grundbausteine</h2>
			<BasicComponents />
		</section>

		<section aria-label="Zusammengesetzte Bausteine" class="flex flex-col gap-6">
			<h2 class="font-serif text-[32px] leading-none">Zusammengesetzte Bausteine</h2>
			<Composite />
		</section>
	</div>
</main>
