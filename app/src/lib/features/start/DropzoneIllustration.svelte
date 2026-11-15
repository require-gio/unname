<script lang="ts">
	/**
	 * Three stacked sheets in the dropzone (Board "1 · New"). Pure illustration.
	 * Indigo highlights masked content, amber indicates a question.
	 */
	const sheet = 'absolute flex flex-col rounded-md border bg-surface';
</script>

{#snippet line(balken: string[])}
	<span class="flex gap-1.25">
		{#each balken as klass, index (index)}<span class="block rounded-xs {klass}"></span>{/each}
	</span>
{/snippet}

<div aria-hidden="true" class="relative h-44 w-57.5 shrink-0">
	<div
		class="{sheet} top-5.5 left-1.5 h-37 w-29.5 -rotate-9 gap-2 border-line px-3.5 py-4 shadow-float"
	>
		{@render line(['h-1.5 w-[70%] bg-skeleton'])}
		{@render line(['h-1.5 w-[90%] bg-skeleton'])}
		{@render line(['h-1.5 w-[55%] bg-skeleton'])}
		{@render line(['h-1.5 w-[80%] bg-skeleton'])}
	</div>
	<div
		class="{sheet} top-3.5 left-26 h-37 w-29.5 rotate-7 gap-2 border-line px-3.5 py-4 shadow-float"
	>
		{@render line(['h-1.5 w-[60%] bg-skeleton'])}
		{@render line(['h-1.5 w-[85%] bg-skeleton'])}
		{@render line(['h-1.5 w-[75%] bg-skeleton'])}
		{@render line(['h-1.5 w-[40%] bg-skeleton'])}
	</div>
	<div
		class="{sheet} top-0.5 left-13 h-40 w-31.5 rotate-[-1.5deg] gap-2.25 border-line-strong px-3.5 py-4.5 shadow-page"
	>
		{@render line(['h-1.75 w-[28%] bg-skeleton', 'h-1.75 w-[46%] bg-accent'])}
		{@render line(['h-1.75 w-[88%] bg-skeleton'])}
		{@render line(['h-1.75 w-[40%] bg-accent', 'h-1.75 w-[42%] bg-skeleton'])}
		{@render line(['h-1.75 w-[72%] bg-skeleton'])}
		{@render line(['h-1.75 w-[50%] bg-skeleton', 'h-1.75 w-[30%] bg-amber-strong'])}
		{@render line(['h-1.75 w-[64%] bg-skeleton'])}
	</div>
</div>
