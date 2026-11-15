<script lang="ts">
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import type { PatternIn } from '#lib/sidecar/api.gen.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { names } from '#lib/state/names.svelte.js';
	import TryOutPart from './TryOutPart.svelte';
	import TermsPart from './TermsPart.svelte';
	import PatternsPart from './PatternsPart.svelte';
	import NameListsPart from './NameListsPart.svelte';
	import TemplatePart from './TemplatePart.svelte';

	/**
	 * Settings > Names & Terms: always mask name lists, custom terms, custom patterns,
	 * and the template that shares them with the team.
	 */
	let error = $state(false);
	/** Pattern currently being created – the Try Out part tests it. */
	let draft = $state<PatternIn | null>(null);

	$effect(() => {
		const connection = sidecarGate.connection;
		if (!connection) return;
		names
			.load(connection)
			.then(() => (error = false))
			.catch(() => (error = true));
	});
</script>

{#if error}
	<p role="status" class="text-subtle">{t('settings.names.notAvailable')}</p>
{:else if !names.loaded}
	<Skeleton class="h-60 max-w-190" />
{:else}
	<div class="flex max-w-190 flex-col gap-9">
		<NameListsPart />
		<TermsPart />
		<PatternsPart ondraft={(fresh) => (draft = fresh)} />
		<TryOutPart {draft} />
		<TemplatePart />
	</div>
{/if}
