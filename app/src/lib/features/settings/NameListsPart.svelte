<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Badge from '#lib/ui/Badge.svelte';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Menu from '#lib/ui/Menu.svelte';
	import Section from '#lib/ui/Section.svelte';
	import { listenState } from '#lib/settings/dialog.js';
	import type { NameListOut } from '#lib/sidecar/api.gen.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { names } from '#lib/state/names.svelte.js';
	import { showError, gateConnection } from './change.js';

	/**
	 * "Bekannte Namen": person and company lists from CSV, JSON or plain text.
	 * They are guaranteed to be found even when name detection fails.
	 * The shell reads the file; "Jetzt aktualisieren" forces a reload.
	 */
	let menu = $state(false);
	let running = $state(false);
	let remove = $state<NameListOut | null>(null);

	const locale = $derived(i18n.language === 'en' ? 'en-GB' : 'de-DE');
	const listen = $derived(names.listen?.lists ?? []);
	const configured = $derived(names.listen?.configured ?? null);
	const dialog = $derived({
		title: t('settings.names.dialog'),
		filter: t('settings.names.filter')
	});

	async function add(kind: 'persons' | 'organizations'): Promise<void> {
		running = true;
		try {
			if (await names.addList(kind, dialog)) toast(t('settings.names.added'));
		} catch (error) {
			showError(error);
		} finally {
			running = false;
		}
	}

	async function update(): Promise<void> {
		running = true;
		try {
			for (const list of listen) {
				await names.updateList(list.id, {
					...dialog,
					title: t('settings.names.dialog.update', { name: list.source_name })
				});
			}
			toast(t('settings.names.updated'));
		} catch (error) {
			showError(error);
		} finally {
			running = false;
		}
	}

	async function removeConfirmed(list: NameListOut): Promise<void> {
		const connection = gateConnection();
		if (!connection) return;
		try {
			await names.removeList(connection, list.id);
		} catch (error) {
			showError(error);
		}
	}
</script>

<Section title={t('settings.names.listen')} description={t('settings.names.listen.text')}>
	{#if listen.length > 0 || configured}
		<ul class="flex flex-col overflow-hidden rounded-lg border border-line">
			{#if configured}
				<li class="flex items-center gap-4 border-b border-line-soft px-4 py-3 last:border-b-0">
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="font-semibold">{t('settings.names.configured')}</span>
						<span class="text-label text-muted">
							{t('settings.names.num', { n: configured.count })}
						</span>
					</span>
				</li>
			{/if}
			{#each listen as list (list.id)}
				<li class="flex items-center gap-3 border-b border-line-soft px-4 py-3 last:border-b-0">
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="truncate font-semibold">
							{t(list.kind === 'persons' ? 'settings.names.people' : 'settings.names.companies')} ·
							{list.source_name}
						</span>
						<span class="text-label text-muted">
							{t('settings.names.num', { n: list.count })} · {listenState(
								list.updated_at,
								new Date(),
								t,
								locale
							)}
						</span>
					</span>
					<Badge tone={list.stale ? 'amber' : 'green'}>
						{list.stale ? t('settings.names.stale') : t('settings.names.current')}
					</Badge>
					<IconButton
						label={t('settings.names.remove', { name: list.source_name })}
						icon="delete"
						size="sm"
						variant="quiet"
						onclick={() => (remove = list)}
					/>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="rounded-lg border border-dashed border-line-dash px-4 py-3.5 text-label text-muted">
			{t('settings.names.no')}
		</p>
	{/if}
	<div class="flex flex-wrap items-center justify-between gap-3">
		<span class="text-label text-subtle">{t('settings.names.encrypted')}</span>
		<div class="flex gap-2">
			<Menu
				bind:open={menu}
				alignment="end"
				entries={[
					{ label: t('settings.names.new.people'), onselect: () => void add('persons') },
					{ label: t('settings.names.new.companies'), onselect: () => void add('organizations') }
				]}
			>
				{#snippet trigger(props)}
					<Button {...props} size="sm" icon="new" busy={running}>
						{t('settings.names.add')}
					</Button>
				{/snippet}
			</Menu>
			<Button
				size="sm"
				icon="restore"
				disabled={listen.length === 0 || running}
				onclick={() => void update()}
			>
				{t('settings.names.update')}
			</Button>
		</div>
	</div>
</Section>

<ConfirmDialog
	bind:open={() => remove !== null, (open) => !open && (remove = null)}
	title={t('settings.names.remove.title')}
	description={t('settings.names.remove.text', { name: remove?.source_name ?? '' })}
	confirm={t('common.remove')}
	danger
	onconfirm={() => {
		const list = remove;
		if (list) void removeConfirmed(list);
	}}
/>
