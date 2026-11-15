<script lang="ts">
	import Button from '#lib/ui/Button.svelte';
	import Field from '#lib/ui/Field.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Section from '#lib/ui/Section.svelte';
	import Select from '#lib/ui/Select.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { errorSentence } from '#lib/settings/dialog.js';
	import type { PatternIn } from '#lib/sidecar/api.gen.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { names, type KindOfInfo } from '#lib/state/names.svelte.js';
	import { errorCode, showError, gateConnection } from './change.js';
	import { KINDS, KIND_TEXT, PLACEHOLDER } from './kinds.js';

	/**
	 * Custom patterns for fixed-format numbers. Validated in the sidecar
	 * with its regex engine: patterns that match everything, the empty
	 * string, or are too costly are rejected and not shown.
	 *
	 * `ondraft` reports the pattern being created – the preview already
	 * validates it.
	 */
	let { ondraft }: { ondraft: (draft: PatternIn | null) => void } = $props();

	let open = $state(false);
	let name = $state('');
	let expression = $state('');
	let kind = $state<KindOfInfo>('CUSTOMER_NUMBER');
	let error = $state<string | null>(null);
	let running = $state(false);

	const kinds = $derived(KINDS.map((value) => ({ value, label: t(KIND_TEXT[value]) })));

	$effect(() => {
		ondraft(
			open && expression.trim()
				? { name: name.trim() || '…', regex: expression, entity_type: kind, enabled: true }
				: null
		);
	});

	function close(): void {
		open = false;
		name = '';
		expression = '';
		error = null;
	}

	async function add(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		const connection = gateConnection();
		if (!connection || running || !name.trim() || !expression) return;
		running = true;
		error = null;
		try {
			await names.savePattern(connection, [
				...names.pattern,
				{ name: name.trim(), regex: expression, entity_type: kind, enabled: true }
			]);
			close();
		} catch (reason) {
			error = errorSentence(errorCode(reason), t);
		} finally {
			running = false;
		}
	}

	async function toggle(id: string, on: boolean): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			await names.togglePattern(connection, id, on);
		} catch (reason) {
			showError(reason);
		}
	}

	async function remove(id: string): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			await names.removePattern(connection, id);
		} catch (reason) {
			showError(reason);
		}
	}
</script>

<Section title={t('settings.pattern')} description={t('settings.pattern.text')}>
	{#if names.pattern.length > 0}
		<ul class="flex flex-col overflow-hidden rounded-lg border border-line">
			{#each names.pattern as pattern (pattern.id)}
				{@const type = pattern.entity_type ?? 'CUSTOMER_NUMBER'}
				<li class="flex items-center gap-3 border-b border-line-soft px-4 py-3 last:border-b-0">
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="font-semibold">{pattern.name}</span>
						<code class="truncate font-mono text-caption text-ink-2">{pattern.regex}</code>
					</span>
					<span class="text-label text-muted">
						{t('settings.pattern.becomes', { placeholder: t(PLACEHOLDER[type]) })}
					</span>
					<Switch
						checked={pattern.enabled ?? true}
						label={t('settings.pattern.use', { name: pattern.name })}
						oncheckedchange={(on) => void toggle(pattern.id, on)}
					/>
					<IconButton
						label={t('settings.pattern.remove', { name: pattern.name })}
						icon="delete"
						size="sm"
						variant="quiet"
						onclick={() => void remove(pattern.id)}
					/>
				</li>
			{/each}
		</ul>
	{/if}
	{#if open}
		<form
			class="flex flex-col gap-3 rounded-lg border border-line bg-surface-3 px-4 py-4"
			onsubmit={add}
		>
			<div class="grid grid-cols-[repeat(auto-fit,minmax(min(200px,100%),1fr))] gap-3">
				<Field label={t('settings.pattern.name')}>
					{#snippet children({ id })}
						<TextInput {id} bind:value={name} maxlength={80} />
					{/snippet}
				</Field>
				<Field label={t('settings.kind')}>
					{#snippet children({ id })}
						<Select {id} bind:value={kind} options={kinds} class="w-full" />
					{/snippet}
				</Field>
			</div>
			<Field
				label={t('settings.pattern.expression')}
				notice={t('settings.pattern.expression.notice')}
				error={error ?? undefined}
			>
				{#snippet children({ id, describedby, invalid })}
					<TextInput
						{id}
						bind:value={expression}
						aria-describedby={describedby}
						{invalid}
						maxlength={500}
						spellcheck={false}
						autocomplete="off"
						class="font-mono"
					/>
				{/snippet}
			</Field>
			<div class="flex justify-end gap-2">
				<Button variant="ghost" onclick={close}>{t('common.cancel')}</Button>
				<Button
					type="submit"
					variant="primary"
					disabled={!name.trim() || !expression}
					busy={running}
				>
					{t('settings.pattern.add')}
				</Button>
			</div>
		</form>
	{:else}
		<Button size="sm" icon="new" class="self-start" onclick={() => (open = true)}>
			{t('settings.pattern.new')}
		</Button>
	{/if}
</Section>
