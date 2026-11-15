<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import Dialog from '#lib/ui/Dialog.svelte';
	import Field from '#lib/ui/Field.svelte';
	import Select from '#lib/ui/Select.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { readTemplate } from '#lib/sidecar/settings.js';
	import { saveTextFile } from '#lib/sidecar/save.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { names } from '#lib/state/names.svelte.js';
	import { showError, gateConnection } from './change.js';

	/**
	 * "Template": terms, patterns and masking defaults grouped under a name —
	 * can be saved, exported as a file, or imported (YAML v4). Name lists are excluded
	 * because they contain customer data and must stay on the device.
	 *
	 * Choosing a template replaces existing terms and patterns; if any are present,
	 * a confirmation dialog is shown first.
	 */
	const WITHOUT = '';
	const selectionId = $props.id();

	let replace = $state<string | null>(null);
	let saveOpen = $state(false);
	let title = $state('');
	let running = $state(false);

	const templates = $derived(names.templates?.templates ?? []);
	const active = $derived(names.templates?.active ?? WITHOUT);
	const options = $derived([
		...templates.map((template) => ({
			value: template.id,
			label: template.example
				? t('settings.template.example', { title: template.title })
				: template.title
		})),
		{ value: WITHOUT, label: t('settings.template.without') }
	]);
	const activeTitle = $derived(templates.find((template) => template.id === active)?.title);

	function select(id: string): void {
		if (id === WITHOUT) {
			const connection = gateConnection();
			if (connection) void names.withoutTemplate(connection).catch(showError);
			return;
		}
		if (names.terms.length > 0 || names.pattern.length > 0) {
			replace = id;
			return;
		}
		void apply(id);
	}

	async function apply(id: string): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			await names.selectTemplate(connection, id);
		} catch (error) {
			showError(error);
		}
	}

	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		const connection = gateConnection();
		if (!connection || !title.trim()) return;
		running = true;
		try {
			await names.saveTemplate(connection, title.trim());
			toast(t('settings.template.saved', { title: title.trim() }));
			saveOpen = false;
			title = '';
		} catch (error) {
			showError(error);
		} finally {
			running = false;
		}
	}

	async function doExport(): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			const file = await names.templateExport(connection, t('settings.template.standardTitle'));
			const path = await saveTextFile(
				t('settings.template.export.dialog'),
				file.name,
				file.content
			);
			if (path) toast(t('settings.template.exported'));
		} catch (error) {
			showError(error);
		}
	}

	async function doImport(): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			const file = await readTemplate({
				title: t('settings.template.import.dialog'),
				filter: t('settings.template.import.filter')
			});
			if (!file) return;
			await names.importTemplate(connection, file.content);
			toast(t('settings.template.imported'));
		} catch (error) {
			showError(error);
		}
	}
</script>

<section class="flex flex-col gap-3 border-t border-line-soft pt-6">
	<div class="flex flex-col gap-0.5">
		<label for={selectionId} class="text-lead font-semibold">{t('settings.template')}</label>
		<span class="text-ui text-pretty text-muted">{t('settings.template.text')}</span>
	</div>
	<div class="flex flex-wrap items-center gap-2">
		<Select
			id={selectionId}
			value={active}
			{options}
			class="min-w-60"
			onchange={(event) => select(event.currentTarget.value)}
		/>
		<Button size="md" onclick={() => (saveOpen = true)}>{t('settings.template.save')}</Button>
		<Button size="md" icon="save" onclick={() => void doExport()}>
			{t('settings.template.export')}
		</Button>
		<Button size="md" icon="upload" onclick={() => void doImport()}>
			{t('settings.template.import')}
		</Button>
	</div>
</section>

<ConfirmDialog
	bind:open={() => replace !== null, (open) => !open && (replace = null)}
	title={t('settings.template.replace.title')}
	description={t('settings.template.replace.text')}
	sequences={[
		t('settings.template.replace.terms', { n: names.terms.length }),
		t('settings.template.replace.pattern', { n: names.pattern.length }),
		t('settings.template.replace.listen')
	]}
	confirm={t('settings.template.replace.ja')}
	onconfirm={() => {
		const id = replace;
		if (id) void apply(id);
	}}
/>

<Dialog
	bind:open={saveOpen}
	width="sm"
	title={t('settings.template.save.title')}
	description={t('settings.template.save.text')}
>
	<form id="template-save" onsubmit={save}>
		<Field label={t('settings.template.name')}>
			{#snippet children({ id })}
				<TextInput {id} bind:value={title} maxlength={120} placeholder={activeTitle} />
			{/snippet}
		</Field>
	</form>
	{#snippet fuss()}
		<Button variant="ghost" onclick={() => (saveOpen = false)}>{t('common.cancel')}</Button>
		<Button
			type="submit"
			form="template-save"
			variant="primary"
			disabled={!title.trim()}
			busy={running}
		>
			{t('settings.template.save.go')}
		</Button>
	{/snippet}
</Dialog>
