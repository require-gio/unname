<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { ModeWatcher } from 'mode-watcher';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import CoachMarks from '#lib/ui/CoachMarks.svelte';
	import Toaster from '#lib/ui/Toaster.svelte';
	import favicon from '#lib/assets/favicon.svg';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	let { children } = $props();

	// Global gate for the whole app; it tracks active users so a view that also logs in
	// does not shut it down on exit.
	onMount(() => {
		const started = sidecarGate.start();
		return () => {
			void started.then((abmelden) => abmelden());
		};
	});

	// Switch to initial setup: the shell reports missing name detection or language,
	// and the first finding redirects there.
	// Only once per session: "Set up later" keeps history readable and restore possible;
	// the notice above the content returns to setup.
	let weicheGenommen = false;
	$effect(() => {
		if (weicheGenommen || sidecarGate.snapshot.setup_required !== true) return;
		weicheGenommen = true;
		if (page.route.id !== '/setup') void goto(resolve('/setup'), { replaceState: true });
	});
</script>

<svelte:head>
	<title>{t('app.name')}</title>
	<link rel="icon" href={favicon} />
</svelte:head>

<ModeWatcher />
<Toaster />
<CoachMarks />

{@render children()}
