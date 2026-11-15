<script lang="ts">
	import Button from '#lib/ui/Button.svelte';
	import Dialog from '#lib/ui/Dialog.svelte';
	import Field from '#lib/ui/Field.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { displayReference } from '#lib/history/history.js';
	import { patchEntry } from '#lib/sidecar/api-v2.js';
	import type { Entry } from '#lib/sidecar/api.gen.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { history } from '#lib/state/history.svelte.js';

	/**
	 * Rename an entry from the history (plan 3.7). Sends only `{ title }`
	 * through the existing PATCH; the sidecar sets `title_custom`, so later
	 * processing does not overwrite it. Status, masking, reference and draft
	 * revisions stay untouched.
	 */
	let {
		entry,
		open = $bindable(false),
		onrenamed,
		onCloseFocus
	}: {
		entry: Entry | null;
		open?: boolean;
		onrenamed?: (entry: Entry) => void;
		onCloseFocus?: (event: Event) => void;
	} = $props();

	/** The sidecar's limit (`PatchEntryRequest.title`), counted in code points like Python. */
	const MAX_LENGTH = 200;

	let value = $state('');
	let pending = $state(false);
	let error = $state<string | undefined>(undefined);
	let preparedFor: string | null = null;

	// Prepopulate once per opening; a failed attempt keeps the edited text.
	$effect(() => {
		if (!open) {
			preparedFor = null;
			return;
		}
		if (entry && preparedFor !== entry.id) {
			preparedFor = entry.id;
			value = entry.title;
			error = undefined;
		}
	});

	const reference = $derived(entry ? displayReference(entry) : '');

	async function submit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!entry || pending) return;
		const title = value.trim();
		if (!title) {
			error = t('history.rename.blank');
			return;
		}
		if ([...title].length > MAX_LENGTH) {
			error = t('history.rename.tooLong', { n: MAX_LENGTH });
			return;
		}
		const connection = sidecarGate.connection;
		if (!connection) {
			error = t('history.rename.failed');
			return;
		}
		pending = true;
		error = undefined;
		try {
			const renamed = await patchEntry(connection, entry.id, { title });
			history.upsert(renamed);
			entryViewModel.acceptTitle(renamed);
			open = false;
			onrenamed?.(renamed);
		} catch {
			error = t('history.rename.failed');
		} finally {
			pending = false;
		}
	}
</script>

<Dialog
	bind:open
	title={t('history.rename.title')}
	description={t('history.rename.description', { reference })}
	width="sm"
	{onCloseFocus}
>
	<form id="rename-entry" novalidate onsubmit={submit} class="flex flex-col gap-2">
		<Field label={t('history.rename.field')} {error}>
			{#snippet children({ id, describedby, invalid })}
				<TextInput
					{id}
					bind:value
					aria-describedby={describedby}
					{invalid}
					autocomplete="off"
					spellcheck={false}
					required
				/>
			{/snippet}
		</Field>
		<p role="alert" class="sr-only">{error ?? ''}</p>
	</form>
	{#snippet fuss()}
		<Button variant="ghost" onclick={() => (open = false)}>{t('common.cancel')}</Button>
		<Button variant="primary" type="submit" form="rename-entry" busy={pending}>
			{t('history.rename.save')}
		</Button>
	{/snippet}
</Dialog>
