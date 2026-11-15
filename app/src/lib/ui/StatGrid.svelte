<script lang="ts">
	/**
	 * Display numbers in the heading style so they catch the eye during runtime.
	 */
	let { title, values }: { title: string; values: readonly { num: number; label: string }[] } =
		$props();

	const id = $props.id();
</script>

<section aria-labelledby={id} class="flex flex-col gap-3">
	<h2 {id} class="text-label font-semibold tracking-[0.02em] text-muted uppercase">{title}</h2>
	<ul class="flex flex-wrap gap-2">
		{#each values as value (value.label)}
			<li
				class="inline-flex items-baseline gap-2 rounded-full border border-line bg-surface px-3.5 py-2"
			>
				<span class="font-serif text-heading leading-none tabular-nums">{value.num}</span>
				<span class="text-muted">{value.label}</span>
			</li>
		{/each}
	</ul>
</section>
