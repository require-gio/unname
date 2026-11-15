<script lang="ts">
	import Button from '#lib/ui/Button.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Section from '#lib/ui/Section.svelte';
	import Select from '#lib/ui/Select.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { t } from '#lib/state/i18n.svelte.js';
	import { names, type KindOfInfo } from '#lib/state/names.svelte.js';
	import { showError, gateConnection } from './change.js';
	import { KINDS, KIND_TEXT } from './kinds.js';

	/** "Custom terms": words that are always masked, e.g., project names. */
	let text = $state('');
	let kind = $state<KindOfInfo>('OTHER');
	let running = $state(false);
	const inputId = $props.id();

	const kinds = $derived(KINDS.map((value) => ({ value, label: t(KIND_TEXT[value]) })));
	const valid = $derived(text.trim().length >= 3);

	async function add(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		const connection = gateConnection();
		if (!connection || !valid || running) return;
		running = true;
		try {
			await names.addTerm(connection, text.trim(), kind);
			text = '';
		} catch (error) {
			showError(error);
		} finally {
			running = false;
		}
	}

	async function remove(id: string): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			await names.removeTerm(connection, id);
		} catch (error) {
			showError(error);
		}
	}
</script>

<Section title={t('settings.terms')} description={t('settings.terms.text')}>
	{#if names.terms.length > 0}
		<ul class="flex flex-wrap gap-2" aria-label={t('settings.terms')}>
			{#each names.terms as term (term.id)}
				<li
					class="inline-flex items-center gap-2 rounded-full border border-tint-line bg-tint-2 py-1 pr-1 pl-3 text-label"
				>
					<span class="font-semibold text-tint-ink">{term.text}</span>
					<span class="text-muted"
						>{t('settings.terms.as', { kind: t(KIND_TEXT[term.entity_type]) })}</span
					>
					<IconButton
						label={t('settings.terms.remove', { term: term.text })}
						icon="close"
						size="sm"
						variant="quiet"
						iconSize={14}
						onclick={() => void remove(term.id)}
					/>
				</li>
			{/each}
		</ul>
	{/if}
	<form class="flex flex-wrap items-center gap-2" onsubmit={add}>
		<label for={inputId} class="sr-only">{t('settings.terms.new')}</label>
		<TextInput
			id={inputId}
			bind:value={text}
			placeholder={t('settings.terms.placeholder')}
			maxlength={200}
			class="min-w-60 flex-1"
		/>
		<Select bind:value={kind} options={kinds} aria-label={t('settings.kind')} />
		<Button type="submit" size="md" disabled={!valid} busy={running}>
			{t('settings.terms.add')}
		</Button>
	</form>
</Section>
