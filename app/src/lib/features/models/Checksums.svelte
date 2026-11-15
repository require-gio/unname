<script lang="ts">
	import { addressShort, componentText } from '#lib/models/setup.js';
	import type { ModelPart } from '#lib/sidecar/models.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * Shows which files are loaded and their SHA-256 checksums from the signed app manifest.
	 */
	let { components }: { components: readonly ModelPart[] } = $props();
</script>

<div class="flex flex-col gap-4 overflow-x-auto font-mono text-caption text-ink-2">
	{#each components as part (part.id)}
		<div class="flex flex-col gap-1">
			<span class="font-sans text-label font-semibold text-ink">
				{t(componentText(part).name)}
				<span class="font-normal text-subtle">· {part.origin} · {part.license}</span>
			</span>
			{#each part.files as file (file.path)}
				<span class="whitespace-nowrap">
					{file.path}{#if file.url}&nbsp;· {addressShort(file.url)}{/if}
				</span>
				<span class="whitespace-nowrap text-subtle">sha256 {file.sha256}</span>
			{/each}
		</div>
	{/each}
	<span class="font-sans text-label text-subtle">{t('models.checksums.notice')}</span>
</div>
