<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';
	import { LABELS } from '#lib/sidecar/result-policy.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Badge, { type BadgeTone } from '#lib/ui/Badge.svelte';
	import Button, { type ButtonVariant } from '#lib/ui/Button.svelte';
	import Card, { type CardTone } from '#lib/ui/Card.svelte';
	import Chip from '#lib/ui/Chip.svelte';
	import ChipSelect from '#lib/ui/ChipSelect.svelte';
	import CoachMark from '#lib/ui/CoachMark.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import Dialog from '#lib/ui/Dialog.svelte';
	import DropZone from '#lib/ui/DropZone.svelte';
	import Field from '#lib/ui/Field.svelte';
	import FileBadge from '#lib/ui/FileBadge.svelte';
	import HelpButton from '#lib/ui/HelpButton.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Kbd from '#lib/ui/Kbd.svelte';
	import Logo from '#lib/ui/Logo.svelte';
	import MaskLabel from '#lib/ui/MaskLabel.svelte';
	import Menu from '#lib/ui/Menu.svelte';
	import Meter from '#lib/ui/Meter.svelte';
	import OptionCards from '#lib/ui/OptionCards.svelte';
	import Popover from '#lib/ui/Popover.svelte';
	import Progress from '#lib/ui/Progress.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import Select from '#lib/ui/Select.svelte';
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import Textarea from '#lib/ui/Textarea.svelte';
	import Tooltip from '#lib/ui/Tooltip.svelte';
	import { ICONS, type IconName } from '#lib/ui/icons.js';
	import Chapter from './Chapter.svelte';

	const BUTTON_VARIANTS: ButtonVariant[] = [
		'primary',
		'secondary',
		'ghost',
		'link',
		'tint',
		'danger'
	];
	const BADGE_TONES: BadgeTone[] = ['neutral', 'tint', 'ink', 'amber', 'green', 'red', 'accent'];
	const CARD_TONES: CardTone[] = ['standard', 'accent', 'amber', 'danger', 'quiet', 'tint'];
	const ICON_NAMES = [...Object.keys(ICONS), 'lockFull'] as IconName[];

	let input = $state<'documents' | 'text'>('documents');
	let appearance = $state<'light' | 'dark' | 'system'>('system');
	let returnPath = $state<'text' | 'file'>('text');
	let view = $state<'result' | 'nextTo' | 'original'>('result');
	let method = $state<'reversible' | 'irreversible'>('reversible');
	let switchA = $state(true);
	let switchB = $state(false);
	let switchC = $state(true);
	let names = $state(true);
	let places = $state(false);
	let info = $state<'name' | 'place' | 'company'>('name');
	let language = $state<'de' | 'en'>('de');
	let policy = $state<'schutz' | 'keep' | 'vorschlagen'>('schutz');
	let style = $state<'codes' | 'surrogates'>('codes');
	let term = $state('');
	let source = $state('hf');
	let retention = $state('30');
	let fehlerhaft = $state('PERSON_??');
	let documentText = $state(
		'Hallo Frau Hofer,\n\nanbei wie besprochen das Angebot für die Einbauküche.'
	);
	let answer = $state('Sehr geehrte Frau <<PERSON_51c2e8d4>>,');
	let popoverOpen = $state(false);
	let menuOpen = $state(false);
	let dialogOpen = $state(false);
	let followUpOpen = $state(false);
</script>

<div class="flex flex-col gap-10">
	<div class="grid grid-cols-2 gap-10">
		<Chapter title="Button · Varianten und Größen">
			<div class="flex flex-col gap-3">
				{#each ['sm', 'md', 'lg'] as const as size (size)}
					<div class="flex flex-wrap items-center gap-2">
						{#each BUTTON_VARIANTS as variant (variant)}
							<Button {variant} {size}>{variant}</Button>
						{/each}
					</div>
				{/each}
				<div class="flex flex-wrap items-center gap-2">
					<Button variant="primary" icon="upload">Dateien auswählen</Button>
					<Button variant="primary" iconRight="next">Prüfen</Button>
					<Button variant="secondary" busy>Speichert</Button>
					<Button variant="secondary" disabled>Gesperrt</Button>
					<Button variant="secondary" href="#components">Als Verweis</Button>
					<Button variant="tint" icon="ai">Alle KI-Vorschläge übernehmen</Button>
				</div>
				<div class="flex flex-wrap items-center gap-2 self-start rounded-lg bg-bar p-1.5">
					<Button variant="bar" size="sm">Verkleinern</Button>
					<Button variant="bar" size="sm" icon="regler">Maskierung</Button>
				</div>
			</div>
		</Chapter>

		<Chapter title="IconButton · HelpButton · Tooltip">
			<div class="flex flex-wrap items-center gap-2">
				<IconButton label={t('sidebar.collapse')} icon="sidebar" />
				<IconButton label={t('common.close')} icon="close" variant="quiet" />
				<IconButton label="Weitere Dokumente hinzufügen" icon="new" variant="link" size="sm" />
				<IconButton label={t('sidebar.new')} icon="new" variant="secondary" />
				<IconButton label="Zurück zur Übersicht" icon="back" size="sm" />
				<IconButton label="Rund" icon="info" form="rund" variant="secondary" />
				<span class="inline-flex rounded-lg bg-bar p-1">
					<IconButton label="Vergrößern" icon="larger" variant="bar" size="sm" />
				</span>
				<HelpButton />
				<HelpButton size="md" />
				<Tooltip text="Original – oder Leertaste gedrückt halten">
					{#snippet trigger(props)}
						<Button {...props} variant="ghost" size="sm" icon="info">Mit Tooltip</Button>
					{/snippet}
				</Tooltip>
			</div>
			<div class="flex flex-wrap items-start gap-6 pt-6 pl-1">
				<span
					class="rounded-md border border-dashed border-line-dash px-3 py-2 text-label text-muted"
					{@attach coachmark(1, 'help.start.masking', { page: 'right' })}
				>
					Ziel einer Hilfe-Marke (? schaltet sie ein)
				</span>
			</div>
		</Chapter>
	</div>

	<div class="grid grid-cols-2 gap-10">
		<Chapter title="Segmented · hell, Leiste, Karte">
			<div class="flex flex-col items-start gap-3">
				<Segmented
					label="Was soll geschützt werden?"
					size="lg"
					bind:value={input}
					options={[
						{ value: 'documents', label: 'Dokumente', icon: 'document' },
						{ value: 'text', label: 'Text', icon: 'text' }
					]}
				/>
				<Segmented
					label={t('settingsDialog.appearance')}
					bind:value={appearance}
					options={[
						{ value: 'light', label: t('settingsDialog.appearance.light') },
						{ value: 'dark', label: t('settingsDialog.appearance.dark') },
						{ value: 'system', label: t('settingsDialog.appearance.system') }
					]}
				/>
				<Segmented
					label="Art der Eingabe"
					size="sm"
					bind:value={returnPath}
					options={[
						{ value: 'text', label: 'Text' },
						{ value: 'file', label: 'Datei' }
					]}
				/>
				<span class="inline-flex rounded-xl bg-bar p-1.5 shadow-bar">
					<Segmented
						label="Ansicht"
						variant="bar"
						onlyActiveBeschriftet
						bind:value={view}
						options={[
							{ value: 'result', label: 'Ergebnis', icon: 'document' },
							{ value: 'nextTo', label: 'Nebeneinander', icon: 'sideBySide' },
							{ value: 'original', label: 'Original', icon: 'text' }
						]}
					/>
				</span>
				<Segmented
					label={t('masking.kind')}
					variant="map"
					bind:value={method}
					class="w-[390px]"
					options={[
						{
							value: 'reversible',
							label: t('masking.reversible'),
							subline: t('masking.reversible.short'),
							icon: 'restore'
						},
						{
							value: 'irreversible',
							label: t('masking.irreversible'),
							subline: t('masking.irreversible.short'),
							icon: 'lock'
						}
					]}
				/>
			</div>
		</Chapter>

		<Chapter title="Switch · Chip · ChipAuswahl">
			<div class="flex flex-col gap-4">
				<div class="flex flex-wrap items-center gap-4">
					<Switch bind:checked={switchA} label="Klein an" />
					<Switch bind:checked={switchB} label="Klein aus" />
					<Switch bind:checked={switchC} label="Groß an" size="md" />
					<Switch checked={false} label="Groß aus" size="md" />
					<Switch checked label="Gesperrt" disabled />
				</div>
				<div class="flex flex-wrap items-center gap-1.5">
					<Chip on={names} num={5} onclick={() => (names = !names)}>{t('category.names')}</Chip>
					<Chip on={places} num={1} onclick={() => (places = !places)}
						>{t('category.locations')}</Chip
					>
					<Chip on locked>{t('category.finance_ids')}</Chip>
					<Chip on size="md">DE</Chip>
					<Chip on={false} size="md">CH</Chip>
				</div>
				<ChipSelect
					label={t('selection.kind')}
					bind:value={info}
					options={[
						{ value: 'name', label: t('selection.kind.name') },
						{ value: 'place', label: t('selection.kind.place') },
						{ value: 'company', label: t('selection.kind.company') }
					]}
				/>
			</div>
		</Chapter>
	</div>

	<div class="grid grid-cols-3 gap-10">
		<Chapter title="Badge · flach">
			<div class="flex flex-col gap-2.5">
				<div class="flex flex-wrap gap-1.5">
					{#each BADGE_TONES as tone (tone)}<Badge {tone}>{tone}</Badge>{/each}
				</div>
				<div class="flex flex-wrap gap-3">
					<Badge tone="green" flat>{t('component.installed')}</Badge>
					<Badge tone="amber" flat>bitte aktualisieren</Badge>
					<Badge tone="neutral" flat>benötigt</Badge>
					<Badge tone="tint" icon="ai">{t('component.recommended')}</Badge>
				</div>
			</div>
		</Chapter>
		<Chapter title="FileBadge · klein, mittel, groß">
			<div class="flex flex-col gap-2.5">
				{#each ['small', 'mittel', 'large'] as const as size (size)}
					<div class="flex flex-wrap items-center gap-2">
						{#each ['a.pdf', 'b.docx', 'c.xlsx', 'd.jpg', 'e.txt'] as name (name)}
							<FileBadge {name} {size} />
						{/each}
					</div>
				{/each}
			</div>
		</Chapter>
		<Chapter title="MaskLabel">
			<div class="flex flex-col items-start gap-2">
				{#each LABELS as label (label)}<MaskLabel {label} />{/each}
				<MaskLabel label="reversible" inventedNames />
			</div>
		</Chapter>
	</div>

	<Chapter title="Card · Töne">
		<div class="grid grid-cols-6 gap-3">
			{#each CARD_TONES as tone (tone)}
				<Card {tone}
					><span class="font-semibold">{tone}</span><span class="text-label text-muted">Inhalt</span
					></Card
				>
			{/each}
		</div>
	</Chapter>

	<div class="grid grid-cols-2 gap-10">
		<Chapter title="OptionCards · Raster mit Standard, Sprache">
			<OptionCards
				label="Standard-Maskierung"
				bind:value={method}
				standardMark
				options={[
					{
						value: 'reversible',
						title: t('masking.reversible'),
						text: t('masking.reversible.explanation'),
						example: 'Sabine Hofer → <<PERSON_ba903855>>',
						technicalTerm: t('masking.reversible.technicalTerm')
					},
					{
						value: 'irreversible',
						title: t('masking.irreversible'),
						text: t('masking.irreversible.explanation'),
						example: 'Sabine Hofer → [Name]',
						technicalTerm: t('masking.irreversible.technicalTerm')
					}
				]}
			/>
			<OptionCards
				layout="language"
				label={t('settingsDialog.appLanguage')}
				bind:value={language}
				options={[
					{ value: 'de', title: 'Deutsch', text: 'German', shortCode: 'DE' },
					{ value: 'en', title: 'English', text: 'Englisch', shortCode: 'EN' }
				]}
			/>
		</Chapter>
		<Chapter title="OptionCards · Liste">
			<OptionCards
				layout="list"
				label={t('masking.style')}
				bind:value={style}
				options={[
					{
						value: 'codes',
						title: t('masking.style.codes'),
						example: '<<PERSON_…>>',
						text: t('masking.style.codes.explanation')
					},
					{
						value: 'surrogates',
						title: t('masking.style.surrogates'),
						example: 'Petra Lang',
						text: t('masking.style.surrogates.explanation')
					}
				]}
			/>
			<OptionCards
				layout="list"
				label="Entscheidungen der KI"
				bind:value={policy}
				options={[
					{
						value: 'schutz',
						title: 'Nur Richtung Schutz',
						text: 'Die KI darf maskieren, nie behalten.'
					},
					{
						value: 'keep',
						title: 'Auch behalten ab 95 %',
						text: 'Sehr sichere Fälle bleiben stehen.'
					},
					{
						value: 'vorschlagen',
						title: 'Nur vorschlagen',
						text: 'Sie entscheiden jede Stelle selbst.'
					}
				]}
			/>
		</Chapter>
	</div>

	<div class="grid grid-cols-3 gap-10">
		<Chapter title="Field · TextInput · Select">
			<div class="flex flex-col gap-3">
				<Field label="Quelle" notice="Nur während eines Downloads offen.">
					{#snippet children({ id, describedby })}
						<Select
							{id}
							aria-describedby={describedby}
							bind:value={source}
							options={[
								{ value: 'hf', label: 'Hugging Face' },
								{ value: 'gh', label: 'GitHub Releases' }
							]}
						/>
					{/snippet}
				</Field>
				<Field label="Muster" error="Das Muster ist kein gültiger Ausdruck.">
					{#snippet children({ id, describedby, invalid })}
						<TextInput {id} aria-describedby={describedby} {invalid} bind:value={fehlerhaft} />
					{/snippet}
				</Field>
				<div class="flex flex-wrap items-center gap-2">
					<TextInput
						variant="pille"
						placeholder="Begriff hinzufügen …"
						aria-label="Neuer Begriff"
						bind:value={term}
					/>
					<Select
						aria-label="Löschfrist"
						bind:value={retention}
						options={[
							{ value: '7', label: 'nach 7 Tagen' },
							{ value: '30', label: 'nach 30 Tagen' }
						]}
					/>
				</div>
			</div>
		</Chapter>
		<Chapter title="Textarea · Dokument, Codes">
			<div class="flex flex-col gap-3">
				<Textarea font="document" aria-label="Ihr Text" rows={3} bind:value={documentText} />
				<Textarea
					font="codes"
					aria-label="Antwort der KI"
					rows={2}
					placeholder="Antwort der KI hier einfügen …"
					bind:value={answer}
				/>
			</div>
		</Chapter>
		<Chapter title="Progress · Meter">
			<div class="flex flex-col gap-4">
				<Progress label="Läuft" value={64} />
				<Progress label="Fertig" value={100} tone="ink" />
				<Progress label="Unsicher" value={40} tone="neutral" />
				<div class="rounded-md bg-tint-1 px-3 py-2.5">
					<Meter value={0.84} label={t('question.localAi')} valueText="84 %" icon="ai" />
				</div>
				<div class="rounded-md bg-surface-3 px-3 py-2.5">
					<Meter
						value={0.52}
						label={t('question.localAi')}
						valueText="52 %"
						icon="ai"
						safe={false}
					/>
				</div>
			</div>
		</Chapter>
	</div>

	<div class="grid grid-cols-2 gap-10">
		<Chapter title="Popover · Menu · Dialog · ConfirmDialog · Toast">
			<div class="flex flex-wrap items-center gap-2">
				<Popover bind:open={popoverOpen} label={t('masking.adjust')}>
					{#snippet trigger(props)}
						<Button {...props} icon="regler" iconRight="aufklappen">{t('masking.adjust')}</Button>
					{/snippet}
					<p class="text-label text-muted">Inhalt eines Popovers.</p>
				</Popover>
				<Menu
					bind:open={menuOpen}
					title="Name · 4 Stellen"
					entries={[
						{ label: t('code.onlyHereKeep'), onselect: () => {} },
						{ label: t('code.allKeep', { category: 'Namen' }), onselect: () => {} }
					]}
				>
					{#snippet trigger(props)}
						<Button {...props} variant="secondary">Code-Menü</Button>
					{/snippet}
				</Menu>
				<Button onclick={() => (dialogOpen = true)}>Dialog</Button>
				<Button variant="danger" onclick={() => (followUpOpen = true)}>Verlauf leeren …</Button>
				<Button variant="ghost" onclick={() => toast.success(t('common.copied'))}>Toast</Button>
			</div>
			<Dialog
				bind:open={dialogOpen}
				title="Weitere Dokumente hinzufügen"
				description="Sie kommen zu den 3 Dateien dazu."
			>
				<p class="text-muted">Inhalt des Dialogs.</p>
				{#snippet fuss()}
					<Button variant="ghost" onclick={() => (dialogOpen = false)}>{t('common.cancel')}</Button>
					<Button variant="primary" onclick={() => (dialogOpen = false)}
						>{t('dropzone.files')}</Button
					>
				{/snippet}
			</Dialog>
			<ConfirmDialog
				bind:open={followUpOpen}
				title="Verlauf leeren?"
				description="Alle Einträge im Verlauf werden gelöscht."
				sequences={[
					'Die Dateilisten der Einträge sind danach weg.',
					'Die Übersetzungstabelle bleibt – Rückübersetzen funktioniert weiter.'
				]}
				confirm="Verlauf leeren"
				danger
				onconfirm={() => {}}
			/>
		</Chapter>
		<Chapter title="Kbd · Spinner · Skeleton · Logo">
			<div class="flex flex-wrap items-center gap-4">
				<Kbd>⏎</Kbd>
				<Kbd>Alt</Kbd><Kbd>N</Kbd>
				<Spinner />
				<Spinner size={20} label={t('common.loads')} />
				<div class="flex w-28 flex-col gap-1.5">
					<Skeleton class="w-3/4" /><Skeleton class="w-full" /><Skeleton
						tone="masked"
						class="w-1/2"
					/><Skeleton tone="question" class="w-1/3" />
				</div>
			</div>
			<div class="flex flex-wrap items-center gap-6">
				<Logo size={16} />
				<Logo size={28} />
				<Logo variant="full" size={28} />
				<span class="inline-flex rounded-lg bg-side p-3"><Logo variant="full" size={40} /></span>
			</div>
		</Chapter>
	</div>

	<Chapter title="DropZone · hero, kompakt, inline, gesperrt">
		<div class="grid grid-cols-[1.4fr_1fr] items-start gap-4">
			<DropZone
				text={t('dropzone.text')}
				formats={t('dropzone.formats')}
				onfiles={() => {}}
				onfolder={() => {}}
			>
				{#snippet illustration()}
					<div class="relative h-44 w-[230px]">
						<div
							class="absolute top-5.5 left-1.5 flex h-37 w-29.5 -rotate-9 flex-col gap-2 rounded-md border border-line bg-surface px-3.5 py-4 shadow-float"
						>
							<Skeleton class="w-[70%]" /><Skeleton class="w-[90%]" /><Skeleton class="w-[55%]" />
						</div>
						<div
							class="absolute top-3.5 left-26 flex h-37 w-29.5 rotate-7 flex-col gap-2 rounded-md border border-line bg-surface px-3.5 py-4 shadow-float"
						>
							<Skeleton class="w-[60%]" /><Skeleton class="w-[85%]" /><Skeleton class="w-[40%]" />
						</div>
						<div
							class="absolute top-0.5 left-13 flex h-40 w-31.5 -rotate-[1.5deg] flex-col gap-2.25 rounded-md border border-line-strong bg-surface px-3.5 py-4.5 shadow-pop"
						>
							<span class="flex gap-1.25"
								><Skeleton class="h-1.75 w-[28%]" /><Skeleton
									tone="masked"
									class="h-1.75 w-[46%]"
								/></span
							>
							<Skeleton class="h-1.75 w-[88%]" />
							<span class="flex gap-1.25"
								><Skeleton tone="masked" class="h-1.75 w-[40%]" /><Skeleton
									class="h-1.75 w-[42%]"
								/></span
							>
							<span class="flex gap-1.25"
								><Skeleton class="h-1.75 w-[50%]" /><Skeleton
									tone="question"
									class="h-1.75 w-[30%]"
								/></span
							>
						</div>
					</div>
				{/snippet}
			</DropZone>
			<DropZone
				variant="kompakt"
				title={t('dropzone.further')}
				text="Sie kommen zu den 3 Dateien dazu und werden mit derselben Maskierung geprüft."
				formats="PDF, Word, Excel, CSV, Text, Fotos und Scans"
				active
				onfiles={() => {}}
			/>
			<DropZone
				variant="inline"
				text="Oder eine Datei von der KI hierher ziehen – Word, Excel, Markdown, Text"
				onfiles={() => {}}
			/>
			<DropZone
				variant="kompakt"
				title={t('dropzone.title')}
				locked="Die lokale Verarbeitung startet noch."
			/>
		</div>
	</Chapter>

	<div class="grid grid-cols-2 gap-10">
		<Chapter title="CoachMark · alle Seiten">
			<div class="flex flex-wrap items-start gap-6 py-2">
				<CoachMark number={1} page="bottom" text={t('help.start.masking')} />
				<CoachMark number={2} page="top" text={t('help.restore.storage')} />
				<CoachMark number={3} page="right" text={t('help.review.mark')} />
				<CoachMark number={4} page="left" text={t('help.history.retention', { days: 30 })} />
			</div>
		</Chapter>
		<Chapter title="Icon">
			<div class="flex flex-wrap items-center gap-3 text-ink-2">
				{#each ICON_NAMES as name (name)}
					<span
						class="inline-flex size-8 items-center justify-center rounded-sm bg-surface"
						title={name}
					>
						<Icon {name} size={18} />
					</span>
				{/each}
			</div>
		</Chapter>
	</div>
</div>
