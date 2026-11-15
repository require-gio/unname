<script lang="ts" module>
	/**
	 * Logo D2 · fade version. Paths taken unchanged from
	 * `unname-logo/icon/unname-icon.svg` and `unname-logo/logo/unname-wordmark.svg`;
	 * colors come from `--brand` / `--brand-ink`, the wordmark uses
	 * `currentColor`. No separate asset is shipped.
	 */
	const PIXEL =
		'M200 236h72v72h-72z M200 316h72v72h-72z M200 396h72v72h-72z M200 476h72v72h-72z M200 556h72v72h-72z M200 636h72v72h-72z M282 238h68v68h-68z M282 318h68v68h-68z M282 398h68v68h-68z M282 478h68v68h-68z M282 558h68v68h-68z M282 638h68v68h-68z M282 718h68v68h-68z M365 641h62v62h-62z M365 721h62v62h-62z M448 644h56v56h-56z M448 724h56v56h-56z M532 648h48v48h-48z M532 728h48v48h-48z M616 252h40v40h-40z M616 332h40v40h-40z M616 412h40v40h-40z M616 492h40v40h-40z M616 572h40v40h-40z M616 652h40v40h-40z M616 732h40v40h-40z M700 256h32v32h-32z M700 336h32v32h-32z M700 416h32v32h-32z M700 496h32v32h-32z M700 576h32v32h-32z M700 656h32v32h-32z';

	/*
	 * Position of the wordmark relative to the icon in a 1024 × 1024 grid:
	 * offset 0.23 × width, height 0.56 × the icon, centered vertically.
	 */
	const WORD_SCALE = (0.56 * 1024) / 124;
	const WORD_X = 1024 + 0.23 * 1024;
	const WORD_Y = (1024 - 0.56 * 1024) / 2;
	const FULL_WIDTH = WORD_X + 699.6 * WORD_SCALE;

	const WORTMARKE =
		'M264.0 10Q200.0 10 150.5 -17.0Q101.0 -44 73.0 -92.0Q45.0 -140 45.0 -203V-483H177.0V-206Q177.0 -176 187.0 -154.5Q197.0 -133 217.0 -122.0Q237.0 -111 264.0 -111Q305.0 -111 328.0 -136.0Q351.0 -161 351.0 -206V-483H482.0V-203Q482.0 -139 454.5 -91.0Q427.0 -43 377.5 -16.5Q328.0 10 264.0 10Z M876.0 0V-277Q876.0 -320 849.0 -347.0Q822.0 -374 779.0 -374Q751.0 -374 729.0 -362.0Q707.0 -350 694.5 -328.0Q682.0 -306 682.0 -277L631.0 -303Q631.0 -360 655.5 -402.5Q680.0 -445 723.5 -469.0Q767.0 -493 822.0 -493Q875.0 -493 917.0 -466.5Q959.0 -440 983.0 -397.5Q1007.0 -355 1007.0 -306V0ZM551.0 0V-483H682.0V0Z M1403.0 0V-277Q1403.0 -320 1376.0 -347.0Q1349.0 -374 1306.0 -374Q1278.0 -374 1256.0 -362.0Q1234.0 -350 1221.5 -328.0Q1209.0 -306 1209.0 -277L1158.0 -303Q1158.0 -360 1182.5 -402.5Q1207.0 -445 1250.5 -469.0Q1294.0 -493 1349.0 -493Q1402.0 -493 1444.0 -466.5Q1486.0 -440 1510.0 -397.5Q1534.0 -355 1534.0 -306V0ZM1078.0 0V-483H1209.0V0Z M1807.0 10Q1741.0 10 1688.0 -23.0Q1635.0 -56 1605.0 -113.0Q1575.0 -170 1575.0 -241Q1575.0 -313 1605.0 -370.0Q1635.0 -427 1688.0 -460.0Q1741.0 -493 1807.0 -493Q1859.0 -493 1900.0 -472.0Q1941.0 -451 1965.5 -413.5Q1990.0 -376 1992.0 -329V-155Q1990.0 -107 1965.5 -70.0Q1941.0 -33 1900.0 -11.5Q1859.0 10 1807.0 10ZM1831.0 -111Q1886.0 -111 1920.0 -147.5Q1954.0 -184 1954.0 -242Q1954.0 -280 1938.5 -309.5Q1923.0 -339 1895.5 -355.5Q1868.0 -372 1831.0 -372Q1795.0 -372 1767.5 -355.5Q1740.0 -339 1724.5 -309.5Q1709.0 -280 1709.0 -242Q1709.0 -203 1724.5 -173.5Q1740.0 -144 1767.5 -127.5Q1795.0 -111 1831.0 -111ZM1947.0 0V-130L1968.0 -248L1947.0 -364V-483H2077.0V0Z M2158.0 0V-483H2289.0V0ZM2473.0 0V-285Q2473.0 -327 2446.5 -350.5Q2420.0 -374 2381.0 -374Q2355.0 -374 2334.0 -363.5Q2313.0 -353 2301.0 -333.0Q2289.0 -313 2289.0 -285L2238.0 -310Q2238.0 -366 2262.5 -407.0Q2287.0 -448 2329.0 -470.5Q2371.0 -493 2424.0 -493Q2474.0 -493 2515.0 -470.5Q2556.0 -448 2580.0 -407.5Q2604.0 -367 2604.0 -310V0ZM2788.0 0V-285Q2788.0 -327 2761.5 -350.5Q2735.0 -374 2696.0 -374Q2671.0 -374 2649.5 -363.5Q2628.0 -353 2616.0 -333.0Q2604.0 -313 2604.0 -285L2529.0 -303Q2533.0 -361 2560.0 -403.5Q2587.0 -446 2631.0 -469.5Q2675.0 -493 2729.0 -493Q2783.0 -493 2826.0 -470.5Q2869.0 -448 2894.0 -406.0Q2919.0 -364 2919.0 -306V0Z M3221.0 10Q3145.0 10 3086.0 -22.5Q3027.0 -55 2992.5 -112.0Q2958.0 -169 2958.0 -242Q2958.0 -314 2991.5 -370.5Q3025.0 -427 3083.0 -460.5Q3141.0 -494 3212.0 -494Q3282.0 -494 3335.5 -462.5Q3389.0 -431 3419.5 -376.5Q3450.0 -322 3450.0 -253Q3450.0 -240 3448.5 -226.5Q3447.0 -213 3443.0 -196L3037.0 -195V-293L3382.0 -294L3329.0 -253Q3327.0 -296 3313.5 -325.0Q3300.0 -354 3274.5 -369.5Q3249.0 -385 3212.0 -385Q3173.0 -385 3144.0 -367.5Q3115.0 -350 3099.5 -318.5Q3084.0 -287 3084.0 -244Q3084.0 -200 3100.5 -167.5Q3117.0 -135 3148.0 -117.5Q3179.0 -100 3220.0 -100Q3257.0 -100 3287.0 -112.5Q3317.0 -125 3339.0 -150L3416.0 -73Q3381.0 -32 3330.5 -11.0Q3280.0 10 3221.0 10Z';
</script>

<script lang="ts">
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * `marke`: only the icon (collapsed sidebar, favicon size);
	 * `voll`: icon and wordmark side-by-side. Proportions follow the
	 * horizontal logo: wordmark height 0.56 × icon height, offset 0.23 ×.
	 *
	 * With `dekorativ` (default) the name is omitted as text or for
	 * screen readers; otherwise the logo gets `role="img"` and the name.
	 */
	let {
		variant = 'mark',
		size = 28,
		dekorativ = true,
		class: className
	}: {
		variant?: 'mark' | 'full';
		size?: number;
		dekorativ?: boolean;
		class?: string;
	} = $props();
</script>

<span
	role={dekorativ ? undefined : 'img'}
	aria-label={dekorativ ? undefined : t('app.name')}
	aria-hidden={dekorativ ? 'true' : undefined}
	class={cn('inline-flex shrink-0 items-center text-ink', className)}
>
	<svg
		width={variant === 'full' ? (size * FULL_WIDTH) / 1024 : size}
		height={size}
		viewBox={variant === 'full' ? `0 0 ${FULL_WIDTH} 1024` : '0 0 1024 1024'}
		overflow="visible"
		class="shrink-0"
		aria-hidden="true"
	>
		<rect
			width="1024"
			height="1024"
			rx="228"
			class="fill-brand stroke-brand-ring"
			stroke-width="1"
			vector-effect="non-scaling-stroke"
		/>
		<path class="fill-brand-ink" d={PIXEL} />
		<rect x="785" y="341" width="22" height="22" opacity="0.7" class="fill-brand-ink" />
		<rect x="785" y="581" width="22" height="22" opacity="0.7" class="fill-brand-ink" />
		<rect x="869" y="425" width="14" height="14" opacity="0.45" class="fill-brand-ink" />
		{#if variant === 'full'}
			<g transform="translate({WORD_X} {WORD_Y}) scale({WORD_SCALE})">
				<g transform="translate(3.071 110.016) scale(0.198413)">
					<path fill="currentColor" d={WORTMARKE} />
				</g>
			</g>
		{/if}
	</svg>
</span>
