<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import Select from '#lib/ui/Select.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { errorKey } from '#lib/models/setup.js';
	import { istModelError, type ModelSource } from '#lib/sidecar/models.js';
	import { models } from '#lib/state/models.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * "Where models are loaded from" (Settings › Models & Storage). The shell remembers the choice and
	 * only contacts the hosts of that source when downloading.
	 */
	let {
		id,
		withFile = false,
		label
	}: {
		id: string;
		withFile?: boolean;
		label: string;
	} = $props();

	const choice = $derived(models.overview?.choice);
	let address = $state('');
	let ownOpen = $state(false);

	$effect(() => {
		if (choice?.source === 'custom') address = choice.custom_url ?? '';
	});

	const options = $derived([
		{ value: 'huggingface' as const, label: t('models.source.huggingface') },
		{ value: 'github' as const, label: t('models.source.github') },
		{ value: 'custom' as const, label: t('models.source.own') },
		...(withFile ? [{ value: 'file' as const, label: t('models.source.file') }] : [])
	]);
	const selected = $derived<ModelSource>(
		ownOpen ? 'custom' : ((choice?.source ?? 'huggingface') as ModelSource)
	);

	async function set(source: ModelSource, url?: string): Promise<void> {
		try {
			await models.source({ source: source, custom_url: url ?? null });
			ownOpen = false;
		} catch (error) {
			toast.error(t(errorKey(istModelError(error) ? error.code : undefined)));
		}
	}

	function select(source: ModelSource): void {
		if (source === 'custom') {
			// Save only after a valid address is entered.
			ownOpen = true;
			return;
		}
		void set(source);
	}
</script>

<span class="flex flex-wrap items-center gap-2">
	<label for={id} class="text-label text-muted">{label}</label>
	<Select
		{id}
		value={selected}
		{options}
		disabled={models.running}
		onchange={(event) => select(event.currentTarget.value as ModelSource)}
	/>
</span>
{#if selected === 'custom'}
	<form
		class="flex w-full flex-wrap items-center gap-2"
		onsubmit={(event) => {
			event.preventDefault();
			void set('custom', address);
		}}
	>
		<label for="{id}-address" class="sr-only">{t('models.source.address')}</label>
		<TextInput
			id="{id}-address"
			bind:value={address}
			placeholder="https://modelle.firma.example/unname"
			class="min-w-64 flex-1"
			inputmode="url"
			autocomplete="off"
			spellcheck={false}
		/>
		<Button size="sm" type="submit" disabled={!address.trim()}>
			{t('models.source.accept')}
		</Button>
	</form>
{/if}
