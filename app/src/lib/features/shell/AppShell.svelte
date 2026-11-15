<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import { cn } from '#lib/utils.js';
	import HelpButton from '#lib/ui/HelpButton.svelte';
	import GateBanner from './GateBanner.svelte';
	import Sidebar from './Sidebar.svelte';

	/**
	 * Layout for all views except the initial setup:
	 * sidebar left, content right, "?" top-right, with a notice about local processing above.
	 * Content scrolls, sidebar stays fixed.
	 *
	 * The review view occupies the full area: it scrolls independently, has its own floating bars
	 * and places the "?" next to the file tabs. The restore view puts the "?" in its navigation line.
	 */
	let { children }: { children: Snippet } = $props();

	const fullArea = $derived(page.route.id === '/(app)/entry/[id]/review');
	const ownHelp = $derived(fullArea || page.route.id === '/(app)/entry/[id]/restore');
</script>

<div class="flex h-svh min-h-0 bg-bg text-ink">
	<Sidebar />
	<main
		class={cn(
			'relative min-w-0 flex-1',
			fullArea ? 'flex flex-col overflow-hidden' : 'overflow-y-auto'
		)}
	>
		{#if !ownHelp}<HelpButton class="absolute top-4.5 right-5.5 z-10" />{/if}
		<GateBanner />
		{@render children()}
	</main>
</div>
