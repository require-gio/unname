<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { afterNavigate, goto, onNavigate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import SettingsDialog from '#lib/features/settings/SettingsDialog.svelte';
	import AppShell from '#lib/features/shell/AppShell.svelte';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import {
		listenOpened,
		guardEntryClose,
		listenMenu,
		setMenu,
		menuTexts
	} from '#lib/sidecar/system.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { help } from '#lib/state/help.svelte.js';
	import { historyView } from '#lib/state/history-view.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { start } from '#lib/state/start.svelte.js';
	import { history } from '#lib/state/history.svelte.js';

	let { children } = $props();

	/** Ongoing detections: how often the history queries. */
	const NACHFRAGEN_MS = 2000;

	onNavigate(({ from, to }) => {
		// File tabs change only the query; the entry and its queues remain open.
		if (from?.url.pathname === to?.url.pathname) return;
		return entryViewModel.flush().catch((error) => {
			toast.error(t('entry.flushFailed'));
			throw error;
		});
	});

	// "Back to history" only applies while staying in the opened entry.
	afterNavigate(({ from, to }) =>
		historyView.track(from ? { routeId: from.route.id, entryId: from.params?.id } : null, {
			routeId: to?.route.id ?? null,
			entryId: to?.params?.id
		})
	);

	// History and settings are read-only; they run as soon as a connection exists,
	// even if saving is currently locked.
	$effect(() => {
		const connection = sidecarGate.connection;
		void history.load(connection);
		if (!connection) return;
		void settings.load(connection);
	});

	// The home page masking follows the saved default – regardless of who loaded
	// or changed it. Otherwise new documents would get “Reversible” if the first
	// load failed and the setup or a template applied settings later.
	$effect(() => {
		const saved = settings.values?.masking;
		if (saved) untrack(() => start.acceptPreset(saved));
	});

	// Menu bar uses the app language; `t` reads the language, so the effect
	// runs again after each change.
	$effect(() => {
		void setMenu(menuTexts(t)).catch(() => {
			// Then the default menu remains; the app works the same.
		});
	});

	/** Menu and dock need something from the start page: they handle it there. */
	function onStartPage(request: NonNullable<typeof start.angefordert>): void {
		start.angefordert = request;
		const target = resolve('/(app)');
		if (page.url.pathname !== target) void goto(target);
	}

	onMount(() => {
		const flushOnHide = () => void entryViewModel.flush(true).catch(() => {});
		window.addEventListener('pagehide', flushOnHide);
		const abmelden = [
			guardEntryClose(
				() => entryViewModel.flush(),
				() => toast.error(t('entry.flushFailed'))
			),
			listenMenu((action) => {
				if (action === 'settings') settings.open();
				else if (action === 'help') help.toggle();
				else onStartPage(action === 'open_folder' ? 'folder' : 'files');
			}),
			listenOpened(() => onStartPage('opened'))
		];
		return () => {
			window.removeEventListener('pagehide', flushOnHide);
			abmelden.forEach((ab) => void ab.then((fn) => fn()));
		};
	});

	$effect(() => {
		if (!history.running || !sidecarGate.connection) return;
		const timer = setInterval(() => void history.update(), NACHFRAGEN_MS);
		return () => clearInterval(timer);
	});
</script>

<AppShell>{@render children()}</AppShell>
<SettingsDialog />
