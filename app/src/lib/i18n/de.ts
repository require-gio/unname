/**
 * German dictionary – reference.
 *
 * All visible UI text is defined here and accessed via `t()` in components.
 * Placeholders use curly braces, e.g. `{name}`. An entry with `one`/`other`
 * provides a plural form; the count is supplied as `{n}`.
 *
 * Svelte files reference only keys. The base name of the protected result
 * label must not appear in any Svelte file (`result-policy.spec.ts`), so the
 * switch keys are named `masking.all.*` and the label keys `label.level3.*`.
 */

export type PluralForm = { zero?: string; one: string; other: string };
export type Message = string | PluralForm;

export const de = {
	// General
	'app.name': 'unname',
	'common.close': 'Schließen',
	'common.cancel': 'Abbrechen',
	'common.confirm': 'Bestätigen',
	'common.loads': 'Lädt …',
	'common.copied': 'Kopiert',
	'common.change': 'Ändern',
	'common.remove': 'Entfernen',
	'common.view': 'Ansehen',
	'common.edit': 'Bearbeiten',
	'common.moreSettings': 'Mehr Einstellungen',
	'common.changeStandard': 'Standard ändern',
	'common.standard': 'Standard',
	'common.back': 'Zurück',
	'common.copy': 'Kopieren',

	// Help on demand
	'help.show': 'Hilfe anzeigen',
	'help.title': 'Hilfe',
	'help.start.masking':
		'Hier legen Sie fest, ob Namen wiederherstellbar bleiben – oder ob anonymisiert wird.',
	'help.history.retention':
		'Einträge werden nach {days} Tagen gelöscht, mit den gespeicherten Kopien der Originale. Ändern lässt sich das unter Einstellungen › Verlauf.',
	'help.history.never':
		'Einträge bleiben mit den gespeicherten Kopien der Originale, bis Sie sie löschen. Ändern lässt sich das unter Einstellungen › Verlauf.',
	'help.review.code':
		'Code anklicken, um die Stelle zu behalten. Mauszeiger darauf zeigt das Original.',
	'help.review.mark': 'Fehlt etwas? Text markieren – ein kleines Menü fragt nur nach der Art.',
	'help.review.mark.page':
		'Fehlt etwas? Über die Wörter ziehen – Doppelklick nimmt ein Wort, Dreifachklick die Zeile.',
	'help.review.menu': 'Was insgesamt maskiert wird, stellen Sie hier unter »Maskierung« ein.',
	'help.restore.storage':
		'Die Antwort wird nur im Arbeitsspeicher übersetzt und nicht gespeichert.',
	'help.restore.view':
		'Die Antwort wird nur im Arbeitsspeicher übersetzt und nicht gespeichert. Weitere Antworten lassen sich jederzeit hier einfügen.',
	'help.saved.answer':
		'Den geschützten Text an ChatGPT & Co. geschickt? Antwort hier einfügen – aus <<PERSON_ba903855>> wird wieder der echte Name. Klappt auch Tage später, mit Codes aus allen Einträgen.',

	// Sidebar and history
	'sidebar.collapse': 'Seitenleiste einklappen',
	'sidebar.expand': 'Seitenleiste ausklappen',
	'sidebar.new': 'Neu',
	'sidebar.settings': 'Einstellungen',
	'historyList.title': 'Verlauf',
	'historyList.today': 'Heute',
	'historyList.yesterday': 'Gestern',
	'historyList.empty': 'Noch nichts im Verlauf.',
	'historyList.files': { one: '{n} Datei', other: '{n} Dateien' },
	'historyList.status.processing': 'wird geprüft',
	'historyList.status.review': 'zu prüfen',
	'historyList.status.saved': 'gespeichert',
	'historyList.status.copied': 'kopiert',
	'historyList.status.failed': 'fehlgeschlagen',
	'historyList.status.irreversible': 'irreversibel',
	'historyList.status.level3': 'anonym',
	'historyList.status.changed': 'seit dem Speichern geändert',
	'historyList.notice.size': '{size} gespeichert',
	'historyList.notice.deletion': 'wird am {date} gelöscht',
	'historyList.notice.never': 'wird nicht automatisch gelöscht',
	'historyList.text': 'Text',
	'historyList.restorations': { one: '{n} Rückübersetzung', other: '{n} Rückübersetzungen' },
	'historyList.error': 'Der Verlauf ist gerade nicht verfügbar.',

	// history: navigation and overview
	'history.title': 'Verlauf',
	'history.nav.label': 'Einträge',
	'history.nav.count': { one: '{label}, {n} Eintrag', other: '{label}, {n} Einträge' },
	'history.scope.open': 'Offen',
	'history.scope.completed': 'Abgeschlossen',
	'history.scope.all': 'Alle Einträge',
	'history.recent': 'Zuletzt erstellt',
	'history.status.processing': 'Wird verarbeitet',
	'history.status.review': 'Zu prüfen',
	'history.status.failed': 'Fehler',
	'history.status.changed': 'Seit dem Speichern geändert',
	'history.status.saved': 'Gespeichert',
	'history.status.copied': 'Kopiert',
	'history.format.scan': '{format}-Scan',
	'history.format.more': { one: '+{n} weiteres', other: '+{n} weitere' },
	'history.pages': { one: '{n} Seite', other: '{n} Seiten' },
	'history.extent.empty': 'Keine Dateien',
	'history.reference': 'Referenz',
	'history.count': { one: '{n} Eintrag', other: '{n} Einträge' },
	'history.result.filtered': { one: '{shown} von {n} Eintrag', other: '{shown} von {n} Einträgen' },
	'history.search.label': 'Nach Titel, Referenz, Datum oder Format suchen',
	'history.search.placeholder': 'Titel, Referenz, Datum …',
	'history.search.clear': 'Suche löschen',
	'history.sort.label': 'Sortierung',
	'history.sort.newest': 'Neueste zuerst',
	'history.sort.oldest': 'Älteste zuerst',
	'history.column.entry': 'Eintrag',
	'history.column.extent': 'Umfang',
	'history.column.created': 'Erstellt',
	'history.column.status': 'Stand',
	'history.column.actions': 'Aktionen',
	'history.exact': 'Referenz stimmt genau',
	'history.more': 'Weitere anzeigen',
	'history.loading': 'Verlauf wird geladen …',
	'history.empty.none':
		'Noch nichts im Verlauf. Abgelegte Dokumente und maskierte Texte erscheinen hier.',
	'history.empty.open': 'Gerade ist nichts offen.',
	'history.empty.completed': 'Noch nichts abgeschlossen.',
	'history.empty.search': 'Keine passenden Einträge.',
	'history.error.initial': 'Der Verlauf konnte nicht geladen werden.',
	'history.error.stale':
		'Der Verlauf ließ sich gerade nicht aktualisieren. Angezeigt wird der letzte Stand.',
	'history.retry': 'Erneut versuchen',
	'history.back': 'Zurück zum Verlauf',
	'history.info.label': 'Details zu {title} #{reference}',
	'history.info.created': 'Erstellt am {time}',
	'history.info.storage': 'Speicherplatz und Löschung',
	'history.info.storageUnknown': 'Speicherplatz gerade nicht bekannt',
	'history.rename.action': 'Umbenennen: {title} #{reference}',
	'history.rename.title': 'Eintrag umbenennen',
	'history.rename.description':
		'Der Titel hilft nur beim Wiederfinden und ändert nichts am Dokument. Die Referenz #{reference} bleibt.',
	'history.rename.field': 'Titel',
	'history.rename.save': 'Umbenennen',
	'history.rename.blank': 'Bitte geben Sie einen Titel ein.',
	'history.rename.tooLong': 'Der Titel darf höchstens {n} Zeichen haben.',
	'history.rename.failed': 'Umbenennen hat nicht geklappt. Bitte versuchen Sie es erneut.',
	'history.rename.done': 'Umbenannt in »{title}«.',
	'history.rename.doneHidden': 'Umbenannt in »{title}«. Der Eintrag passt nicht mehr zur Suche.',

	// state of local processing (Fail-Closed-Gate)
	'gate.starts.title': 'Wird vorbereitet',
	'gate.starts.text':
		'Die Erkennung startet. Dokumente können Sie schon ablegen – sie werden erkannt, sobald sie bereit ist.',
	'gate.failure.title': 'Gerade nicht verfügbar',
	'gate.failure.text':
		'Ablegen, Speichern und Kopieren sind gesperrt, bis die lokale Verarbeitung wieder antwortet. Was schon geladen ist, bleibt sichtbar.',
	'gate.outside.title': 'Außerhalb der App',
	'gate.outside.text':
		'Diese Ansicht läuft ohne die App. Hier wird nichts verarbeitet – Ablegen, Speichern und Kopieren bleiben gesperrt.',
	'gate.restart': 'Neu starten',
	'gate.setup.title': 'Einrichtung steht aus',
	'gate.setup.text':
		'Die Erkennungsmodelle fehlen noch. Verlauf und Zurückübersetzen gehen schon; zum Ablegen bitte einmal einrichten.',
	'gate.setup.open': 'Einrichten',

	// Home page
	'start.title.start': 'Persönliche Daten entfernen – ',
	'start.title.emphasized': 'ohne dass etwas',
	'start.title.end': ' dieses Gerät verlässt.',
	'start.text':
		'Namen, Anschriften, Kontonummern und mehr werden erkannt und maskiert. Vorher sehen Sie genau, wie das Ergebnis aussieht.',
	'start.input': 'Was soll geschützt werden?',
	'start.input.documents': 'Dokumente',
	'start.input.text': 'Text',
	'start.mask.reversible.title': 'Namen werden pseudonymisiert',
	'start.mask.reversible.text':
		'Aus »Sabine Hofer« wird ein Code. Die echten Namen lassen sich später wiederherstellen – etwa in der Antwort einer KI.',
	'start.mask.irreversible.title': 'Namen werden irreversibel maskiert',
	'start.mask.irreversible.text':
		'Aus »Sabine Hofer« wird [Name]. Wiederherstellen lässt sich das nicht. Orte und Datumsangaben bleiben stehen.',
	'start.mask.all.title': 'Dokumente werden anonymisiert',
	'start.mask.all.text':
		'Alles wird maskiert, auch Orte und Datumsangaben. Als »anonym« gilt eine Datei erst, wenn die Restrisiko-Prüfung nichts mehr findet.',
	'start.mask.saveError': 'Die Maskierung gilt jetzt, ließ sich aber nicht als Standard speichern.',
	'start.example.original': 'Rechnung.pdf',
	'start.example.protected': 'Rechnung_geschützt.pdf',
	'start.example.salutation': 'Frau',
	'start.example.name': 'Sabine Hofer',
	'start.example.iban': 'IBAN',
	'start.example.place': 'Wien',
	'start.example.date': '3. April 2026',
	'start.example.placeholder.name': '[Name]',
	'start.example.placeholder.iban': '[IBAN]',
	'start.example.placeholder.place': '[Ort]',
	'start.example.placeholder.date': '[Datum]',

	// Text mode
	'text.input': 'Ihr Text',
	'text.input.placeholder':
		'Text hier einfügen oder tippen – etwa eine E-Mail oder eine Chat-Nachricht.',
	'text.insert': 'Aus Zwischenablage einfügen',
	'text.clipboard.empty': 'In der Zwischenablage ist kein Text.',
	'text.output.reversible': 'Pseudonymisiert',
	'text.output.irreversible': 'Irreversibel maskiert',
	'text.live': 'aktualisiert sich beim Tippen',
	'text.running': 'wird aktualisiert …',
	'text.empty': 'Hier erscheint Ihr Text – ohne persönliche Daten.',
	'text.questions.no': 'keine Fragen',
	'text.questions.masked': {
		one: '{n} unsichere Stelle vorsichtshalber maskiert',
		other: '{n} unsichere Stellen vorsichtshalber maskiert'
	},
	'text.restore': 'Antwort zurückübersetzen',
	'text.copied': 'Kopiert – der Text steht ohne persönliche Daten in der Zwischenablage.',
	'text.copy.error': 'Kopieren hat nicht geklappt. Bitte noch einmal versuchen.',
	'text.clipboard.error':
		'Der Text ließ sich nicht in die Zwischenablage legen. Bitte noch einmal kopieren.',
	'text.error': 'Der Text konnte gerade nicht maskiert werden.',
	'text.tooLarge': 'Der Text ist für den Text-Modus zu lang. Als Datei abgelegt geht es.',
	'text.notice':
		'Ideal für E-Mails und Chat-Nachrichten. Derselbe Name bekommt denselben Code wie in Ihren Dokumenten – so klappt auch die Rückübersetzung.',

	// Entry
	'entry.files': 'Dateien',
	'entry.created': 'Angelegt {time}',
	'entry.rejected': 'Nicht übernommen',
	'entry.notFound':
		'Diesen Eintrag gibt es nicht mehr. Vielleicht wurde er gelöscht oder ist abgelaufen.',
	'entry.error': 'Der Eintrag konnte gerade nicht geladen werden.',
	'entry.flushFailed':
		'Der letzte Arbeitsstand konnte nicht gesichert werden. Bitte erneut versuchen.',
	'entry.text.info':
		'Der Text selbst wird nicht gespeichert – nur, dass er kopiert wurde. Die Codes darin lassen sich zurückübersetzen.',
	'entry.text.infoIrreversible':
		'Der Text selbst wird nicht gespeichert – nur, dass er kopiert wurde.',
	'file.ready': 'bereit zum Prüfen',
	'file.photo': 'Foto eines Dokuments',
	'file.previousFound': {
		one: 'bisher {n} Stelle gefunden',
		other: 'bisher {n} Stellen gefunden'
	},
	'file.cancelled': 'abgebrochen',
	'file.expired': 'abgelaufen',
	'file.unavailable': 'nicht mehr lesbar',

	// Reasons for rejected files (`#lib/inputs/rejection.ts`)
	'rejection.format': 'Dieses Format wird nicht unterstützt.',
	'rejection.tooLarge': 'Die Datei ist größer als 50 MB.',
	'rejection.empty': 'Die Datei ist leer.',
	'rejection.unreadable': 'Die Datei ließ sich nicht lesen.',
	'rejection.ocr': 'Für Scans fehlt gerade die Texterkennung.',
	'rejection.analysis': 'Die Erkennung ist fehlgeschlagen.',
	'ocr.notice':
		'Scans und Fotos brauchen die Texterkennung. Laden Sie sie unter Modelle & Speicher und versuchen Sie es danach erneut.',
	'ocr.load': 'Texterkennung laden',
	'rejection.notProvided': 'Die Datei konnte nicht an die lokale Verarbeitung übergeben werden.',
	'rejection.otherwise': 'Die Datei wurde nicht angenommen.',
	'rejection.storage':
		'Die Datei ließ sich nicht sicher in der App ablegen. Ist der Speicher voll?',
	'rejection.cloud':
		'Die Datei liegt nur in iCloud und wird gerade geladen – gleich noch einmal ablegen.',

	// Restore (AI answer restored)
	'restore.navigation': 'Navigation',
	'restore.overview': 'Übersicht',
	'restore.title': 'Antwort zurückübersetzt',
	'restore.text':
		'Die Codes wurden über die gemeinsame Übersetzungstabelle ersetzt – auch Codes aus früheren Einträgen.',
	'restore.input': 'Antwort der KI',
	'restore.kind': 'Art der Eingabe',
	'restore.kind.text': 'Text',
	'restore.kind.file': 'Datei',
	'restore.clear': 'Leeren',
	'restore.placeholder': 'Antwort der KI hier einfügen …',
	'restore.output': 'Mit echten Namen',
	'restore.mark': 'Eingesetzte Stellen markieren',
	'restore.empty': 'Hier erscheint die Antwort mit den echten Namen.',
	'restore.running': 'wird zurückübersetzt …',
	'restore.error': 'Die Codes konnten gerade nicht zurückübersetzt werden.',
	'restore.tooLarge': 'Die Antwort ist zu lang. Als Datei geht es.',
	'restore.count': 'Zählung',
	'restore.codes': { one: '{n} Code', other: '{n} Codes' },
	'restore.replaced': 'ersetzt',
	'restore.here': '{n} von hier',
	'restore.from': '{n} aus »{title}«',
	'restore.further': '{n} aus weiteren Einträgen',
	'restore.earlier': {
		one: '{n} aus einem früheren Eintrag',
		other: '{n} aus früheren Einträgen'
	},
	'restore.changed': 'davon {n} in veränderter Schreibweise',
	'restore.unknown': '{n} unbekannt',
	'restore.unknown.reason':
		'nicht in der Übersetzungstabelle: anderes Gerät oder älterer Schlüssel',
	'restore.noCode': 'Kein Code gefunden – in der Antwort steht nichts zum Zurückübersetzen.',
	'restore.asFile': 'Als Datei speichern',
	'restore.asFile.title': 'Antwort mit echten Namen speichern',
	'restore.asFile.name': 'Antwort_mit_Namen.txt',
	'restore.saved': 'Gespeichert.',
	'restore.saveError': 'Die Antwort ließ sich nicht speichern.',
	'restore.copied': 'Kopiert – mit den echten Namen.',
	'restore.copied.local': 'Kopiert – nur auf diesem Mac, in {minuten} Minuten wieder entfernt.',
	'restore.file.drop': 'Datei von der KI hierher ziehen',
	'restore.file.formats':
		'Word, Excel, Markdown oder Text. Die Codes werden direkt in der Datei ersetzt, die Formatierung bleibt.',
	'restore.file.select': 'Datei wählen',
	'restore.file.dialog': 'Datei von der KI auswählen',
	'restore.file.running': 'Datei wird zurückübersetzt …',
	'restore.file.byAi': 'von der KI',
	'restore.file.found': {
		zero: 'kein Code gefunden',
		one: '{n} Code gefunden',
		other: '{n} Codes gefunden'
	},
	'restore.file.new': 'neue Datei · die Datei der KI bleibt unverändert',
	'restore.file.savedIn': 'gespeichert in {place}',
	'restore.file.noNew': 'Kein bekannter Code – es entsteht keine neue Datei.',
	'restore.file.open': 'Öffnen',
	'restore.file.saveUnder': 'Speichern unter …',
	'restore.file.saveDialog': 'Datei mit echten Namen speichern',
	'restore.file.preview': 'Vorschau',
	'restore.file.shortened': 'Die Vorschau zeigt den Anfang; die Datei enthält alles.',
	'restore.file.several': 'Zurückübersetzt wird eine Datei auf einmal – hier die erste.',
	'restore.file.notReplaceable': {
		one: '{n} Code steht an einer Stelle, die sich nicht sicher ersetzen lässt (etwa in einem Feld oder Blattnamen).',
		other:
			'{n} Codes stehen an Stellen, die sich nicht sicher ersetzen lassen (etwa in Feldern oder Blattnamen).'
	},
	'restore.file.saved': 'Gespeichert – die Datei der KI ist unverändert.',
	'restore.file.openError': 'Die Datei ließ sich nicht öffnen.',
	'restore.file.error.format':
		'Dieses Format lässt sich nicht zurückübersetzen. Möglich sind Word, Excel, Markdown und Text.',
	'restore.file.error.content':
		'Excel-Dateien mit Diagrammen oder Bildern lassen sich nicht unverändert schreiben.',
	'restore.file.error.read': 'Die Datei ließ sich nicht lesen.',
	'restore.file.error.encoding': 'Die Datei ist kein Text in UTF-8.',
	'restore.file.error.empty': 'Die Datei ist leer.',
	'restore.file.error.large': 'Die Datei ist größer als 50 MiB.',
	'restore.file.error.expired':
		'Die zurückübersetzte Datei ist nicht mehr da. Bitte die Datei der KI noch einmal ablegen.',
	'restore.file.error.same':
		'Die Datei der KI wird nicht überschrieben – bitte einen anderen Namen wählen.',
	'restore.file.error.ready': 'Die lokale Verarbeitung antwortet gerade nicht.',
	'restore.file.error.otherwise': 'Die Datei ließ sich nicht zurückübersetzen.',
	'restore.file.error.cloud':
		'Die Datei liegt nur in iCloud und wird gerade geladen – gleich noch einmal wählen.',
	'restore.file.cloud.title': 'In einen synchronisierten Ordner speichern?',
	'restore.file.cloud.text':
		'Der gewählte Ordner liegt in iCloud Drive oder bei einem Cloud-Dienst. Die Datei enthält die echten Namen.',
	'restore.file.cloud.sequence.sync':
		'Die Datei mit Klarnamen wird synchronisiert und verlässt dieses Gerät.',
	'restore.file.cloud.sequence.devices': 'Sie erscheint auf allen Geräten mit diesem Konto.',
	'restore.file.cloud.go': 'Trotzdem speichern',

	// Labels — the sidecar assigns them (`#lib/sidecar/result-policy.ts`)
	'label.reversible': 'Reversibel maskiert',
	'label.reversible.explanation':
		'Codes statt Namen. Über die Übersetzungstabelle lassen sich die echten Namen wiederherstellen.',
	'label.reversible.technicalTerm': 'Pseudonymisierung',
	'label.irreversible': 'Irreversibel maskiert',
	'label.irreversible.explanation':
		'Platzhalter statt Namen, nicht wiederherstellbar. Orte und Daten können noch Hinweise geben.',
	'label.irreversible.technicalTerm': 'Schwärzung – noch nicht anonym',
	'label.level3': 'Anonym',
	'label.level3.explanation':
		'Irreversibel mit »Anonymisieren«: alles wird maskiert, auch Orte und Datumsangaben. Steht nur da, wenn die Restrisiko-Prüfung nichts findet – sonst bleibt es bei »Irreversibel maskiert«.',
	'label.level3.technicalTerm': 'Anonymisierung (nur ohne Befund)',
	'label.inReviewReport': 'Im Prüfbericht: {technicalTerm}',
	'label.inventedNames': 'enthält erfundene Namen',

	// Masking
	'masking.title': 'Maskierung',
	'masking.kind': 'Art der Maskierung',
	'masking.adjust': 'Maskierung anpassen',
	'masking.reversible': 'Reversibel',
	'masking.reversible.short': 'wiederherstellbar',
	'masking.reversible.explanation':
		'Namen werden zu Codes. Die echten Namen lassen sich später wiederherstellen – z. B. in der Antwort einer KI.',
	'masking.reversible.technicalTerm': 'Fachbegriff: Pseudonymisierung',
	'masking.irreversible': 'Irreversibel',
	'masking.irreversible.short': 'nicht wiederherstellbar',
	'masking.irreversible.explanation':
		'Namen werden zu Platzhaltern wie [Name]. Niemand kann sie wiederherstellen.',
	'masking.irreversible.technicalTerm': 'Fachbegriff: Schwärzung',
	'masking.all': 'Anonymisieren',
	'masking.all.from': 'Alles maskieren – auch Orte und Datumsangaben.',
	'masking.all.fromReversible':
		'Alles maskieren – auch Orte und Datumsangaben. Einschalten wechselt auf »Irreversibel«.',
	'masking.all.on':
		'Alles wird maskiert, auch Orte und Datumsangaben. »Anonym« steht erst auf der Datei, wenn die Restrisiko-Prüfung nichts mehr findet.',
	'masking.all.technicalTerm': 'Fachbegriff: Anonymisierung – nur ohne Restrisiko-Befund',
	'masking.whatGetsMasked': 'Was maskiert wird',
	'masking.personDetails': 'Angaben zur Person',
	'masking.circumstances': 'Umstände',
	'masking.circumstances.label': 'Umstände:',
	'masking.applies.new': 'Gilt für alles Neue – beim Prüfen umstellbar.',
	'masking.applies.entry': 'Gilt für alle {n} Dateien dieses Eintrags.',
	'masking.applies.standard': {
		one: 'Ihr Standard · gilt für diese Datei.',
		other: 'Ihr Standard · gilt für alle {n} Dateien.'
	},
	'masking.applies.onlyHere': {
		one: 'Gilt nur für diese Datei.',
		other: 'Gilt nur für diese {n} Dateien.'
	},
	'masking.asStandard': 'Als Standard übernehmen',
	'masking.asStandard.ok': 'Gilt jetzt auch für alles Neue.',
	'masking.asStandard.error': 'Der Standard ließ sich gerade nicht speichern.',
	'masking.places': {
		zero: 'Keine Stelle maskiert',
		one: '{n} Stelle maskiert',
		other: '{n} Stellen maskiert'
	},
	'masking.share': '{share} % des Textes maskiert',
	'masking.always': 'immer',
	'masking.whatHappens': 'Was wird maskiert?',
	'masking.style': 'Wie sieht der Ersatz aus?',
	'masking.style.short': 'Ersatz',
	'masking.style.codes': 'Codes',
	'masking.style.codes.explanation': 'eindeutig und wiederherstellbar',
	'masking.style.surrogates': 'Erfundene Namen',
	'masking.style.surrogates.explanation': 'lesen sich natürlich, bleiben wiederherstellbar',
	'masking.style.placeholder': 'Platzhalter',
	'masking.style.placeholder.explanation': 'die Art der Angabe bleibt erkennbar',
	'masking.style.mask': 'Schwärzen',
	'masking.style.mask.explanation': 'Länge bleibt, Inhalt nicht',
	'masking.style.remove': 'Entfernen',
	'masking.style.remove.explanation': 'die Stelle verschwindet ganz',

	// Categories — chip, singular in menu title, plural in “Keep all …”
	'category.finance_ids': 'Konto-, Ausweis- & Zugangsdaten',
	'category.names': 'Namen',
	'category.addresses': 'Anschriften',
	'category.contact': 'Telefon & E-Mail',
	'category.numbers': 'Kunden- & Aktennummern',
	'category.web': 'Webadressen',
	'category.organizations': 'Firmen',
	'category.locations': 'Orte',
	'category.dates': 'Datumsangaben',
	'category.other_indirect': 'Weitere Merkmale',
	'category.finance_ids.one': 'Konto- oder Ausweisdaten',
	'category.names.one': 'Name',
	'category.addresses.one': 'Anschrift',
	'category.contact.one': 'Kontakt',
	'category.numbers.one': 'Nummer',
	'category.web.one': 'Webadresse',
	'category.organizations.one': 'Firma',
	'category.locations.one': 'Ort',
	'category.dates.one': 'Datum',
	'category.other_indirect.one': 'Merkmal',
	'category.finance_ids.plural': 'Konto-, Ausweis- & Zugangsdaten',
	'category.names.plural': 'Namen',
	'category.addresses.plural': 'Anschriften',
	'category.contact.plural': 'Telefonnummern & E-Mails',
	'category.numbers.plural': 'Kunden- & Aktennummern',
	'category.web.plural': 'Webadressen',
	'category.organizations.plural': 'Firmen',
	'category.locations.plural': 'Orte',
	'category.dates.plural': 'Datumsangaben',
	'category.other_indirect.plural': 'weiteren Merkmale',

	// Questions and decisions
	'question.suspicion': 'Sieht aus wie {was} – wurde aber nicht erkannt',
	'question.uncertain': 'Als {was} erkannt – aber unsicher',
	'question.as.names': 'Name',
	'question.asA.names': 'ein Name',
	'question.as.phone': 'Telefonnummer',
	'question.asA.phone': 'eine Telefonnummer',
	'question.as.email': 'E-Mail-Adresse',
	'question.asA.email': 'eine E-Mail-Adresse',
	'question.as.contact': 'Kontaktangabe',
	'question.asA.contact': 'eine Kontaktangabe',
	'question.as.addresses': 'Anschrift',
	'question.asA.addresses': 'eine Anschrift',
	'question.as.numbers': 'Nummer',
	'question.asA.numbers': 'eine Nummer',
	'question.as.web': 'Webadresse',
	'question.asA.web': 'eine Webadresse',
	'question.as.finance_ids': 'Konto- oder Ausweisnummer',
	'question.asA.finance_ids': 'eine Konto- oder Ausweisnummer',
	'question.as.organizations': 'Firma',
	'question.asA.organizations': 'eine Firma',
	'question.as.locations': 'Ort',
	'question.asA.locations': 'ein Ort',
	'question.as.dates': 'Datum',
	'question.asA.dates': 'ein Datum',
	'question.as.other_indirect': 'persönliches Merkmal',
	'question.asA.other_indirect': 'ein persönliches Merkmal',
	'question.mask': 'Maskieren',
	'question.keep': 'Behalten',
	'question.suggestion': 'Vorschlag',
	'question.localAi': 'Lokale KI',
	'question.safety': '{percent} %',
	'question.number': 'Frage {n}',
	'question.staysAsIs': 'bleibt stehen',
	'question.isMasked': 'wird maskiert',
	'question.maskAnyway': 'Trotzdem maskieren',
	'question.anywayKeep': 'Trotzdem behalten',
	'ai.label.person': 'Wahrscheinlich ein Personenname',
	'ai.label.organization_or_product': 'Wahrscheinlich eine Firma oder ein Produkt',
	'ai.label.location': 'Wahrscheinlich ein Ort',
	'ai.label.term': 'Wahrscheinlich ein Begriff, kein Name',
	'ai.uncertain': 'Unsicher – bitte selbst entscheiden',
	'ai.short.person': 'Name',
	'ai.short.organization_or_product': 'Firma oder Produkt',
	'ai.short.location': 'Ort',
	'ai.short.term': 'Begriff, kein Name',
	'ai.rationale': '{short} · {percent} %',
	'question.open': { one: '{n} offene Frage', other: '{n} offene Fragen' },

	// Codes in document
	'code.original': 'Original: {text}',
	'code.onlyHereKeep': 'Nur hier behalten',
	'code.allKeep': 'Alle {category} behalten',
	'code.maskAgain': 'Wieder maskieren',
	'code.places': { one: '{n} Stelle', other: '{n} Stellen' },
	'code.maskAll': 'Alle {category} maskieren',
	'code.caption': '{kind}: {original}',
	'code.removed': 'Entfernt',

	// Menu at a place
	'menu.addedLater': 'Von Ihnen nachgetragen',
	'menu.adjusted': 'Von Ihnen angepasst',
	'menu.notMask': 'Nicht mehr maskieren',
	'menu.undoAdjustment': 'Anpassung zurücknehmen',
	'menu.byYouMasked': 'Von Ihnen maskiert',
	'menu.byYouKeep': 'Von Ihnen behalten',
	'menu.byAiMasked': 'Von der lokalen KI maskiert',
	'menu.byAiKeep': 'Von der lokalen KI behalten',
	'menu.stayAsIs': '{category} bleiben stehen',

	// Selection menu (add missing location)
	'selection.title': '»{text}« auch maskieren',
	'selection.kind': 'Art der Angabe',
	'selection.kind.name': 'Name',
	'selection.kind.place': 'Ort',
	'selection.kind.company': 'Firma',
	'selection.kind.number': 'Nummer',
	'selection.kind.other': 'Sonstiges',
	'selection.everywhere': {
		one: 'Wird an {n} Stelle maskiert.',
		other: 'Wird an allen {n} Stellen maskiert.'
	},
	'selection.everywhere.files': 'Wird an allen {n} Stellen in {files} Dateien maskiert.',
	'selection.mask': 'Maskieren',
	'selection.expand.title': '»{text}« als eine Stelle maskieren',
	'selection.expand': 'Stelle erweitern',
	'selection.shorten.title': 'Nur »{text}« maskieren',
	'selection.shorten': 'Stelle anpassen',
	'selection.search': 'Was soll zusätzlich maskiert werden?',
	'selection.search.placeholder': 'Text aus dem Dokument',
	'selection.noOccurrence': 'Kommt in diesem Eintrag nicht als ganzes Wort vor.',
	'selection.result': {
		zero: 'Alle Vorkommen waren schon maskiert oder entschieden.',
		one: '{n} Stelle zusätzlich maskiert.',
		other: '{n} Stellen zusätzlich maskiert.'
	},
	'selection.result.files': '{n} Stellen in {files} Dateien zusätzlich maskiert.',
	'addendum.invalid': 'Bitte zuerst eine Textstelle markieren.',
	'addendum.overlaps':
		'Die Markierung überschneidet eine Stelle, die schon maskiert oder entschieden ist.',
	'addendum.unknown': 'Diese Stelle ist nicht mehr bekannt.',
	'addendum.unchanged': 'Die Stelle ist schon genau so markiert.',
	'addendum.several':
		'Die Markierung berührt mehrere Stellen. Bitte eine nach der anderen anpassen.',

	// Detection running
	'detection.title': 'Wird geprüft …',
	'detection.text':
		'Meist dauert das nur Sekunden. Die erste Datei lässt sich schon prüfen, während der Rest läuft.',
	'detection.done.title': 'Bereit zum Prüfen',
	'detection.done.text':
		'Alle Dateien sind durchgesehen. Sehen Sie sich jetzt an, was maskiert wird – und entscheiden Sie die offenen Stellen.',
	'detection.failed.title': 'Das hat nicht geklappt',
	'detection.failed.text': 'Keine der Dateien ließ sich prüfen. Die Gründe stehen an den Dateien.',
	'detection.masking': 'Maskierung: {kind}',
	'detection.masking.standard': 'Maskierung: {kind} (Ihr Standard)',
	'detection.kind.reversible': 'reversibel',
	'detection.kind.irreversible': 'irreversibel',
	'detection.kind.all': 'anonymisieren',
	'detection.review': 'Prüfen',
	'detection.cancelled': 'Die Erkennung wurde abgebrochen.',
	'detection.cancelError': 'Abbrechen hat nicht geklappt. Bitte noch einmal versuchen.',
	'phase.extraction': 'Text wird gelesen',
	'phase.ocr': 'Text wird aus dem Bild gelesen',
	'phase.master_data': 'Namenslisten werden abgeglichen',
	'phase.rules': 'Nummern werden geprüft',
	'phase.ner': 'Namen werden erkannt',
	'phase.suspicion': 'Letzter Blick auf Auffälliges',

	// Review
	'review.document': 'Dokument',
	'review.files': 'Dateien',
	'review.toTheOverview': 'Zurück zur Übersicht',
	'review.further': 'Weitere Dokumente hinzufügen',
	'review.bar': 'Ansicht und Maskierung',
	'review.view': 'Ansicht',
	'review.view.result': 'Ergebnis',
	'review.view.result.title': 'Ergebnis – so wird die Datei',
	'review.view.sideBySide': 'Nebeneinander',
	'review.view.sideBySide.title': 'Original und Ergebnis, Absatz für Absatz',
	'review.view.original': 'Original',
	'review.view.original.title': 'Original – oder Leertaste gedrückt halten',
	'review.appearance': 'Darstellung',
	'review.appearance.page': 'Seite',
	'review.appearance.page.title': 'Seite – das Dokument, wie es aussieht',
	'review.appearance.pageFaithful': 'Seitentreu',
	'review.appearance.pageFaithful.title': 'Seitentreu – Seiten, wie eine Layout-Engine sie setzt',
	'review.pageFaithful.engine': 'Seiten gesetzt mit {engine}. Umbrüche können von Word abweichen.',
	'review.pageFaithful.sets': 'wird neu gesetzt …',
	'review.pageFaithful.loads': 'Seiten werden gesetzt …',
	'review.pageFaithful.error':
		'Die Seiten konnten nicht gesetzt werden. Das Dokument erscheint als „Seite“.',
	'review.pageFaithful.unlocated': {
		one: '{n} Stelle ist hier nicht markiert – die Engine hat ihren Absatz anders gesetzt. „Seite“ oder „Text“ zeigt sie.',
		other:
			'{n} Stellen sind hier nicht markiert – die Engine hat ihre Absätze anders gesetzt. „Seite“ oder „Text“ zeigt sie.'
	},
	'review.header.sideBySide.pageFaithful':
		'Original und Ergebnis, Seite für Seite – Umbrüche können wandern',
	'review.appearance.text': 'Text',
	'review.appearance.text.title': 'Text – nur der Text, Absatz für Absatz',
	'review.page': 'Seite {n} von {total}',
	'review.page.error': 'Das Seitenbild konnte nicht geladen werden.',
	'review.page.hidden': 'Unsichtbarer Text',
	'review.page.hidden.title':
		'Unsichtbarer Text – im Original nicht zu sehen, steckt aber in der Datei',
	'review.page.result': 'Seite {n} von {total} · Ergebnis',
	'review.flow': 'Word-Dokument',
	'review.flow.original': 'Word-Dokument · Original',
	'review.flow.result': 'Word-Dokument · Ergebnis',
	'review.flow.error': 'Das Dokument konnte nicht dargestellt werden. Es erscheint als Text.',
	'review.flow.fonts': {
		one: 'Die Schrift {names} fehlt auf diesem Gerät und ist ersetzt – Zeilen und Seiten können anders brechen als in Word.',
		other:
			'Die Schriften {names} fehlen auf diesem Gerät und sind ersetzt – Zeilen und Seiten können anders brechen als in Word.'
	},
	'review.flow.simplified': {
		one: '{n} Absatz wird vereinfacht dargestellt – seine Formatierung ließ sich nicht sicher zuordnen.',
		other:
			'{n} Absätze werden vereinfacht dargestellt – ihre Formatierung ließ sich nicht sicher zuordnen.'
	},
	'review.header.sideBySide.flow': 'Original und Ergebnis, Seite für Seite',
	'review.grid': 'Tabelle {name}',
	'review.grid.original': 'Tabelle {name} · Original',
	'review.grid.sheets': 'Blätter',
	'review.grid.hidden': 'ausgeblendet',
	'review.grid.conditional':
		'Dieses Blatt hat bedingte Formatierung. Sie wird hier nicht gezeigt, bleibt in der Datei aber erhalten.',
	'review.grid.hiddenNotice':
		'Dieses Blatt ist in Excel ausgeblendet. Es steht trotzdem in der Datei und wird mit maskiert.',
	'review.grid.error': 'Die Tabelle konnte nicht dargestellt werden. Sie erscheint als Text.',
	'review.grid.loads': 'Zeilen werden geladen …',
	'review.header.sideBySide.grid': 'Original und Ergebnis, Zelle für Zelle – scrollen gemeinsam',
	'review.page.original': 'Seite {n} von {total} · Original',
	'review.masking.button': 'Maskierung: {kind}, {places} – ändern',
	'review.zoom.small': 'Verkleinern',
	'review.zoom.larger': 'Vergrößern',
	'review.zoom.value': '{percent} %',
	'review.zoom.pageWidth': 'Seitenbreite',
	'review.zoom.button': '{value} – Seitenbreite ein/aus',
	'review.zoom.width': 'Breite',
	'review.header.original': '{name} · Original',
	'review.header.sideBySide': 'Original und Ergebnis, Absatz für Absatz – scrollen gemeinsam',
	'review.header.sideBySide.page': 'Original und Ergebnis, Seite für Seite',
	'review.column.original': 'Original',
	'review.column.result': 'So wird die Datei',
	'review.keyboard':
		'Codes mit Tab erreichen, Enter öffnet das Menü. Alt+N springt zur nächsten offenen Frage, Alt+M trägt eine Stelle nach. Leertaste halten zeigt das Original.',
	'review.loads': 'Die Datei wird geladen …',
	'review.updated': 'wird aktualisiert …',
	'review.file.running': 'Diese Datei wird noch geprüft …',
	'review.file.error': 'Diese Datei ließ sich nicht prüfen: {reason}',
	'review.file.expired':
		'Diese Datei ist nicht mehr geladen. Die Erkennung hält Dokumente nur eine Weile im Arbeitsspeicher.',
	'review.file.unavailable':
		'Die gespeicherte Kopie dieser Datei ist nicht mehr lesbar. Wählen Sie das Original noch einmal aus.',
	'review.file.chooseOriginal': 'Original auswählen',
	'review.file.cancelled': 'Die Erkennung dieser Datei wurde abgebrochen.',
	'review.file.retry': 'Erneut versuchen',
	'review.file.remove': 'Datei entfernen',
	'review.file.retryError': 'Die Erkennung ließ sich gerade nicht neu starten.',
	'review.file.removeError': 'Die Datei ließ sich gerade nicht entfernen.',
	'review.file.extractionChanged':
		'Die Datei wurde neu ausgelesen, und der Text hat sich geändert. Ihre Entscheidungen gelten nur noch, wo die Stellen gleich geblieben sind.',
	'review.redetect': 'Neu erkennen',
	'review.redetect.text': {
		one: 'Seit dem Erkennen hat sich etwas an der Erkennung geändert ({was}). Neu erkennen nutzt die gespeicherte Kopie; Ihre Entscheidungen bleiben, Codes und erfundene Namen können sich ändern.',
		other:
			'Seit dem Erkennen hat sich bei {n} Dateien etwas an der Erkennung geändert ({was}). Neu erkennen nutzt die gespeicherten Kopien; Ihre Entscheidungen bleiben, Codes und erfundene Namen können sich ändern.'
	},
	'review.redetect.error': 'Die Erkennung ließ sich gerade nicht neu starten.',
	'review.redetect.languages': 'Dokumentsprachen',
	'review.redetect.second_opinion': 'Zweitmeinung',
	'review.redetect.ocr_engine': 'Texterkennung',
	'review.redetect.ner': 'Erkennungsmodell',
	'review.error.load': 'Diese Datei konnte gerade nicht geladen werden.',
	'review.error.apply':
		'Die Maskierung ließ sich gerade nicht berechnen. Was hier steht, ist nicht aktuell.',
	'review.again': 'Noch einmal versuchen',
	'review.reviewBar': 'Prüfen',
	'review.reviewBar.collapse': 'Leiste einklappen',
	'review.reviewBar.expand': 'Leiste ausklappen',
	'review.open.title': {
		zero: 'Alles geprüft.',
		one: '{n} Stelle braucht Ihren Blick',
		other: '{n} Stellen brauchen Ihren Blick'
	},
	'review.open.text': 'Alles andere ist erledigt.',
	'review.done.text': 'Diese Datei ist fertig.',
	'review.other.open': {
		one: 'In einer weiteren Datei ist noch etwas offen.',
		other: 'In {n} weiteren Dateien ist noch etwas offen.'
	},
	'review.other.done': {
		one: 'Die andere Datei hat keine offenen Fragen.',
		other: 'Die anderen {n} Dateien haben keine offenen Fragen.'
	},
	'review.acceptAi': 'Alle KI-Vorschläge übernehmen',
	'review.aiResolved': 'Von der lokalen KI geklärt',
	'review.notice.open': {
		one: '{n} offene Stelle wird beim Speichern maskiert – im Zweifel lieber geschützt.',
		other: '{n} offene Stellen werden beim Speichern maskiert – im Zweifel lieber geschützt.'
	},
	'review.notice.done': 'Alle Stellen entschieden.',
	'review.questionsOpen': { one: '{n} Frage offen', other: '{n} Fragen offen' },
	'review.save': { one: 'Datei speichern', other: 'Alle {n} Dateien speichern' },
	'review.save.short': { one: 'Speichern', other: 'Alle {n} speichern' },
	'review.saveAgain': 'Erneut speichern',
	'review.accepted': {
		one: '{n} Entscheidung übernommen.',
		other: '{n} Entscheidungen übernommen.'
	},
	'review.undo': 'Rückgängig',
	'uploader.title': 'Weitere Dokumente hierher ziehen',
	'uploader.text': {
		one: 'Sie kommen zur Datei dazu und werden mit derselben Maskierung geprüft.',
		other: 'Sie kommen zu den {n} Dateien dazu und werden mit derselben Maskierung geprüft.'
	},
	'uploader.formats': 'PDF, Word, Excel, CSV, Text, Fotos und Scans',
	'uploader.added': {
		one: '{n} Datei hinzugefügt – sie wird jetzt geprüft.',
		other: '{n} Dateien hinzugefügt – sie werden jetzt geprüft.'
	},

	// saved
	'save.folder': 'geschützt',
	'save.addition': '_geschützt',
	'save.dialog': 'Ordner für die geschützten Dateien wählen',
	'save.other': 'In anderen Ordner speichern …',
	'save.running': 'Wird gespeichert …',
	'save.locked.running': 'Speichern geht, sobald die Erkennung fertig ist.',
	'save.locked.nothing': 'Es gibt noch keine Datei zum Speichern.',
	'save.locked.expired': 'Erst die abgelaufenen Originale neu einlesen.',
	'save.locked.unavailable': 'Erst für die nicht mehr lesbaren Dateien das Original auswählen.',
	'save.confirm.title': 'Irreversibel speichern?',
	'save.confirm.text': {
		one: 'Die Datei wird ohne Rückweg maskiert.',
		other: 'Die {n} Dateien werden ohne Rückweg maskiert.'
	},
	'save.confirm.sequence.names':
		'Die echten Namen stehen in den gespeicherten Dateien nicht mehr und lassen sich nicht wiederherstellen.',
	'save.confirm.sequence.original': 'Die Originale bleiben unverändert.',
	'save.confirm.go': 'Irreversibel speichern',
	'save.error': 'Speichern hat nicht geklappt. Es wurde nichts gespeichert.',
	'save.error.pdf':
		'»{name}« ließ sich nicht sicher speichern: Die PDF-Prüfung fand noch Text, der weg sein sollte. Es wurde nichts gespeichert.',
	'save.error.export': '»{name}« ließ sich nicht sicher speichern. Es wurde nichts gespeichert.',
	'save.error.target':
		'In den Zielordner ließ sich nicht schreiben. Bitte einen anderen Ordner wählen.',
	'save.error.expired': 'Einige Originale sind nicht mehr geladen. Bitte zuerst neu einlesen.',
	'save.error.unavailable':
		'Die gespeicherte Kopie einiger Dateien ist nicht mehr lesbar. Bitte das Original auswählen.',
	'save.error.running': 'Die Erkennung läuft noch.',
	'save.error.ready':
		'Die lokale Verarbeitung ist gerade nicht bereit. Bitte gleich noch einmal versuchen.',
	'save.selectFolder': 'Anderen Ordner wählen',
	'save.reimport': 'Neu einlesen',
	'save.tag': 'geschützt',
	'save.cloud':
		'Gespeichert. Der Ordner wird synchronisiert – die maskierten Kopien liegen damit auch in der Cloud.',

	// Saved
	'saved.title': {
		zero: 'Nichts zu speichern',
		one: '{n} Datei gespeichert',
		other: '{n} Dateien gespeichert'
	},
	'saved.in': 'in',
	'saved.originals': 'Originale unverändert',
	'saved.again': 'Nochmal bearbeiten',
	'saved.openFolder': 'Ordner öffnen',
	'saved.folderError': 'Der Ordner ließ sich nicht öffnen.',
	'saved.list': 'Gespeicherte Dateien',
	'saved.unchanged': 'Keine Stelle zu maskieren – keine Kopie nötig',
	'saved.fromImage': 'Text aus dem Bild',
	'saved.fallback': 'Als Text gespeichert – Code im Bild nicht sicher lesbar',
	'saved.scanTextLayer': 'Scan übermalt, mit Textebene',
	'saved.byYou': '{n} von Ihnen entschieden',
	'saved.byAi': '{n} von der lokalen KI geklärt',
	'saved.inDoubt': '{n} im Zweifel maskiert',
	'saved.report': 'Prüfbericht speichern',
	'saved.report.title': 'Prüfbericht speichern',
	'saved.report.ok': 'Prüfbericht gespeichert.',
	'saved.report.error': 'Der Prüfbericht ließ sich nicht speichern.',
	'saved.reimport': 'Originale werden neu eingelesen …',
	'saved.newImported': {
		one: '{n} Original neu eingelesen – es wird jetzt geprüft.',
		other: '{n} Originale neu eingelesen – sie werden jetzt geprüft.'
	},
	'saved.missing': {
		one: 'Ein Original fehlt noch: {names}',
		other: '{n} Originale fehlen noch: {names}'
	},
	'saved.newImportError': 'Die Originale ließen sich nicht neu einlesen.',
	'saved.dialog.originals': 'Originale zum erneuten Einlesen auswählen',
	'saved.changed':
		'Das Original hat sich seit dem ersten Einlesen geändert. Ihre Entscheidungen gelten nur noch, wo die Stellen gleich geblieben sind.',
	'saved.sinceSaving': 'Seit dem Speichern geändert',
	'saved.sinceSaving.text':
		'Die Dateien im Zielordner zeigen den gespeicherten Stand. „Erneut speichern" im Prüfen legt eine neue Kopie daneben; die frühere bleibt.',
	'saved.earlier': 'Frühere Speicherungen',
	'saved.report.short': 'Prüfbericht',
	'saved.openFolder.short': 'Ordner öffnen',
	'saved.last': 'zuletzt {time}',
	'returnPath.eyebrow': 'Jetzt möglich',
	'returnPath.title': 'Antwort einer KI zurückübersetzen',
	'returnPath.input': 'Antwort der KI',
	'returnPath.placeholder': 'Antwort der KI hier einfügen …',
	'returnPath.insert': 'Aus Zwischenablage einfügen',
	'returnPath.go': 'Namen wiederherstellen',
	'returnPath.empty': 'In der Zwischenablage ist kein Text.',
	'returnPath.file': 'Oder eine Datei von der KI hierher ziehen – Word, Excel, Markdown, Text',
	'returnPath.selectFile': 'Datei wählen',
	'final.irreversible.title': 'Irreversibel maskiert – nicht wiederherstellbar',
	'final.irreversible.text':
		'Die echten Namen stehen in diesen Dateien nicht mehr und lassen sich nicht wiederherstellen. Deshalb gibt es hier nichts zurückzuübersetzen.',
	'final.level3.title': 'Anonym – nicht wiederherstellbar',
	'final.level3.text':
		'Alle Angaben sind ersetzt, auch Orte und Datumsangaben, und die Restrisiko-Prüfung hat nichts mehr gefunden. Zurückzuübersetzen gibt es hier nichts.',

	// Review report (`#lib/export/entryreport.ts`) – with technical terms
	'report.title': 'Prüfbericht',
	'report.file': 'Prüfbericht – {title}',
	'report.entry': 'Eintrag',
	'report.entry.title': 'Titel',
	'report.entry.created': 'Angelegt',
	'report.entry.saved': 'Gespeichert',
	'report.entry.generated': 'Erstellt mit',
	'report.entry.version': 'unname {version} · {profile}',
	'report.masking': 'Maskierung',
	'report.masking.kind': 'Methode',
	'report.masking.style': 'Ersatz-Stil',
	'report.masking.categories': 'Kategorien',
	'report.masking.level': 'Schutzstufe (abgeleitet)',
	'report.result': 'Ergebnis',
	'report.result.masked': 'Maskierte Stellen',
	'report.result.byYou': 'Von Ihnen entschieden',
	'report.result.byAi': 'Von der lokalen KI geklärt',
	'report.result.inDoubt': 'Beim Speichern ohne Entscheidung maskiert',
	'report.result.keep': 'Bewusst im Klartext behalten',
	'report.result.byHand': 'Von Hand nachgetragen',
	'report.result.corrected': 'Grenze korrigiert',
	'report.residualRisk': 'Restrisiko-Prüfung',
	'report.residualRisk.clear': 'keine auffällige Merkmalskombination',
	'report.residualRisk.warning': 'Befund – {classes}',
	'report.residualRisk.not_run': 'nicht gelaufen',
	'report.residualRisk.failed': 'nicht auswertbar',
	'residualRisk.date_time': 'Datums- und Zeitangaben',
	'residualRisk.amount_quantity': 'Beträge und Mengen',
	'residualRisk.reference_number': 'Artikel-, Vertrags- und Vorgangsnummern',
	'residualRisk.location_region': 'Orts- und Regionsangaben',
	'residualRisk.rare_term': 'Seltene Fachbegriffe oder Berufsbezeichnungen',
	'report.noLabel': 'ohne Etikett',
	'report.files': 'Dateien',
	'report.file.notSaved': 'nicht gespeichert',
	'report.file.unchanged': 'keine Stelle zu maskieren – keine Kopie',
	'report.file.pdf': {
		one: 'PDF-Prüfung: {n} Seite gerendert',
		other: 'PDF-Prüfung: {n} Seiten gerendert'
	},
	'report.file.checksum': 'SHA-256',
	'report.file.audit': 'Auditeintrag',
	'report.notice.heuristic':
		'Die Restrisiko-Prüfung ist eine Heuristik und ersetzt keine menschliche Bewertung.',
	'report.notice.original':
		'Die Originale wurden nicht verändert; gespeichert wurden neue Dateien.',
	'report.notice.content':
		'Dieser Bericht enthält keine Originalwerte, Ersetzungen oder Textausschnitte.',
	'report.notice.changed':
		'Das Original von »{name}« wurde neu eingelesen und hatte sich seit dem ersten Einlesen geändert.',

	// Ablegen
	'dropzone.title': 'Dateien hierher ziehen',
	'dropzone.text':
		'Eine oder mehrere auf einmal. Die Originale bleiben unverändert – geschützt wird immer eine neue Kopie.',
	'dropzone.files': 'Dateien auswählen',
	'dropzone.folder': 'Ganzen Ordner',
	'dropzone.formats': 'PDF, Word, Excel, CSV, Text, Fotos und Scans · bis 50 MB je Datei',
	'dropzone.release': 'Loslassen zum Hinzufügen',
	'dropzone.locked': 'Ablegen ist gerade nicht möglich',
	'dropzone.further': 'Weitere Dokumente hierher ziehen',
	'dropzone.dialog.files': 'Dateien zum Schützen auswählen',
	'dropzone.dialog.folder': 'Ordner zum Schützen auswählen',
	'dropzone.accept': 'Dateien werden übernommen …',
	'dropzone.queued': {
		one: 'Vorgemerkt – die Datei startet, sobald die Erkennung bereit ist …',
		other: '{n} Dateien vorgemerkt – sie starten, sobald die Erkennung bereit ist …'
	},
	'dropzone.queued.expire':
		'Die Erkennung konnte nicht starten, die vorgemerkten Dateien wurden nicht übernommen. Bitte noch einmal ablegen, sobald sie wieder bereit ist.',
	'dropzone.noFiles': 'In der Auswahl war keine Datei, die sich schützen lässt.',
	'dropzone.skipped': {
		one: 'Eine Datei im Ordner wurde übergangen (anderes Format).',
		other: '{n} Dateien im Ordner wurden übergangen (anderes Format).'
	},
	'dropzone.shortened': 'Mehr als {max} Dateien – nur die ersten {max} sind dabei.',
	'dropzone.error': 'Die Dateien konnten nicht übernommen werden. Bitte noch einmal versuchen.',
	'dropzone.fewSpace':
		'Wenig freier Speicherplatz ({free}). Von jeder Datei bleibt eine verschlüsselte Kopie in der App, bis der Eintrag gelöscht wird.',
	'dropzone.allRejected': {
		one: 'Die Datei wurde nicht angenommen.',
		other: 'Keine der {n} Dateien wurde angenommen.'
	},
	'dropzone.locked.starts': 'Die Erkennung wird noch vorbereitet – gleich geht es los.',
	'dropzone.locked.failure':
		'Die lokale Verarbeitung antwortet gerade nicht. Neue Dokumente sind bis dahin gesperrt.',
	'dropzone.locked.outside': 'Nur in der App möglich – hier wird nichts verarbeitet.',
	'dropzone.locked.setup':
		'Erst die Erkennungsmodelle einrichten – danach bleibt alles auf diesem Gerät.',

	// Files and detection
	'file.done': 'Fertig',
	'file.waits': 'wartet',
	'file.running': 'wird geprüft',
	'file.error': 'fehlgeschlagen',
	'file.pages': { one: '{n} Seite', other: '{n} Seiten' },
	'file.found': { one: '{n} Stelle gefunden', other: '{n} Stellen gefunden' },
	'file.questions': {
		zero: 'keine Fragen',
		one: '{n} braucht Ihren Blick',
		other: '{n} brauchen Ihren Blick'
	},
	'detection.previousFound': 'Bisher gefunden',

	// Models and components
	'component.needed': 'nötig',
	'component.recommended': 'empfohlen',
	'component.installed': 'installiert',
	'component.reviewed': 'geprüft · {size}',
	'component.loads': '{percent} % von {size}',
	'component.waits': 'wartet · {size}',
	'component.skipped': 'übersprungen',

	// Dialoge
	'dialog.confirm.title': 'Sind Sie sicher?',

	// Settings (framework)
	'settingsDialog.title': 'Einstellungen',
	'settingsDialog.live': 'Änderungen gelten sofort.',
	'settingsDialog.appearance': 'Erscheinungsbild',
	'settingsDialog.appearance.light': 'Hell',
	'settingsDialog.appearance.dark': 'Dunkel',
	'settingsDialog.appearance.system': 'Wie System',
	'settingsDialog.appLanguage': 'Sprache der App',
	'settingsDialog.appLanguage.explanation':
		'Steuert Oberfläche und Prüfbericht. Welche Erkennung läuft, entscheidet die Sprache des Dokuments.',

	// settings dialog ("Einstellungen")
	'settings.general': 'Allgemein',
	'settings.general.description': 'Sprache und Darstellung der App.',
	'settings.masking': 'Maskierung',
	'settings.masking.description': 'Standard für alles Neue – in der Vorschau jederzeit umstellbar.',
	'settings.languages': 'Dokumentsprachen',
	'settings.languages.description':
		'In welchen Sprachen und für welche Länder Dokumente geprüft werden.',
	'settings.secondOpinion': 'Zweitmeinung (KI)',
	'settings.secondOpinion.description':
		'Ein kleines KI-Modell hilft bei Stellen, bei denen die Erkennung unsicher ist.',
	'settings.names': 'Namen & Begriffe',
	'settings.names.description':
		'Eigene Namenslisten, Begriffe und Muster – als Vorlage speicherbar.',
	'settings.history': 'Verlauf',
	'settings.history.description': 'Wie lange Einträge im Verlauf bleiben.',
	'settings.table': 'Übersetzungstabelle',
	'settings.table.description':
		'Eine gemeinsame Tabelle für alle Einträge – die Grundlage fürs Zurückübersetzen.',
	'settings.models': 'Modelle & Speicher',
	'settings.models.description': 'Was auf diesem Gerät installiert ist – und woher es kommt.',
	'settings.technical': 'Technik',
	'settings.technical.description': 'Für Fehlersuche und Support.',
	'settings.never': 'nie',
	'settings.follows': 'folgt',
	'settings.bundled': 'mitgeliefert',

	'settings.general.language.text': 'Menüs, Hinweise und Prüfbericht erscheinen in dieser Sprache.',
	'settings.general.language.more': 'Weitere Sprachen folgen',
	'settings.general.language.notice':
		'Die Sprache der App ändert nicht, wie Dokumente geprüft werden – ein englisches Dokument wird auch in der deutschen App als Englisch erkannt.',
	'settings.general.toLanguages': 'Zu den Dokumentsprachen →',
	'settings.general.release': 'Bildschirmfreigabe',
	'settings.general.release.title': 'Fenster in Bildschirmfreigaben ausblenden',
	'settings.general.release.text':
		'macOS zeigt das Fenster dann in Bildschirmfotos und Freigaben nicht. Nicht jedes Aufnahmeprogramm hält sich daran – verlassen Sie sich in Videokonferenzen nicht allein darauf.',
	'settings.general.release.error': 'Die Einstellung ließ sich nicht übernehmen.',

	'settings.masking.example': 'So sieht das aus',

	'settings.languages.detect': 'Sprache jedes Absatzes automatisch erkennen',
	'settings.languages.detect.text':
		'Auch gemischte Dokumente: Deutsche und englische Absätze werden jeweils mit Regeln, Stoppwörtern und Sprachmodell ihrer Sprache geprüft. Aus: eine Sprache je Dokument.',
	'settings.languages.scope': 'Gilt für alles, was ab jetzt erkannt wird.',
	'settings.languages.packages': 'Sprachpakete',
	'settings.languages.packages.text':
		'Die Namenserkennung ist mehrsprachig. Ein Sprachpaket bringt dazu Satzzerlegung, Stopp- und Verdachtswörter, Datumsformate, Platzhalter und erfundene Namen seiner Sprache.',
	'settings.languages.package.de': 'Deutsch',
	'settings.languages.package.en': 'English',
	'settings.languages.de':
		'Satzzerlegung (spaCy, klein), Stopp- und Verdachtswörter, Monatsnamen samt »Jänner«, Texterkennung deu',
	'settings.languages.en':
		'Satzzerlegung (spaCy, klein), Stopp- und Verdachtswörter, Monatsnamen, Texterkennung eng',
	'settings.languages.review': 'Dokumente auf {language} prüfen',
	'settings.languages.last': 'Mindestens eine Sprache bleibt eingeschaltet.',
	'settings.languages.installed': 'installiert',
	'settings.languages.loads': 'lädt · {percent} %',
	'settings.languages.load': 'Sprachpaket laden …',
	'settings.languages.continue': 'Weiterladen',
	'settings.languages.missing':
		'Paket fehlt – bis es da ist, zerlegt ein anderes Sprachmodell diese Absätze.',
	'settings.languages.onlyFile':
		'Laden aus dem Internet ist abgeschaltet. Ein Modellpaket installieren Sie unter Modelle & Speicher.',
	'settings.languages.countries': 'Länder für Nummern und Ausweise',
	'settings.languages.countries.text':
		'Steuer-, Sozialversicherungs- und Ausweisnummern hängen am Land, nicht an der Sprache: Ein englischer Vertrag aus Wien enthält österreichische Nummern.',
	'settings.languages.countries.last': 'Mindestens ein Land bleibt eingeschaltet.',
	'settings.country.DE': 'Deutschland',
	'settings.country.AT': 'Österreich',
	'settings.country.CH': 'Schweiz',
	'settings.country.UK': 'Vereinigtes Königreich',
	'settings.country.US': 'USA',
	'settings.languages.placeholder': 'Platzhalter und erfundene Namen in der Sprache des Dokuments',
	'settings.languages.placeholder.text':
		'Ein englischer Absatz bekommt [Company], ein deutscher [Firma]. Aus: alles in der Sprache der App. Codes wie <<PERSON_…>> sind in allen Sprachen gleich.',

	'settings.secondOpinion.switch': 'Zweitmeinung bei unsicheren Stellen',
	'settings.secondOpinion.text':
		'Ein kleines Modell auf diesem Gerät beurteilt Stellen, bei denen die Erkennung unsicher ist. Jede Entscheidung bleibt sichtbar und umkehrbar.',
	'settings.secondOpinion.scope': 'Gilt für alles, was ab jetzt erkannt wird.',
	'settings.secondOpinion.model': 'Modell',
	'settings.secondOpinion.model.text':
		'Klassifikator mit vier festen Antworten: Person, Firma oder Produkt, Ort, Begriff.',
	'settings.secondOpinion.missing':
		'Nicht installiert – bis dahin erscheinen unsichere Stellen als Fragen ohne Einschätzung.',
	'settings.secondOpinion.load': 'Modell laden …',
	'settings.secondOpinion.policy': 'Wie viel die KI selbst entscheidet',
	'settings.secondOpinion.policy.suggest': 'Nichts – nur Vorschläge',
	'settings.secondOpinion.policy.suggest.text':
		'Sie entscheiden jede unsichere Stelle selbst. Die KI zeigt in jeder Frage, was sie vermutet; mit „Alle KI-Vorschläge übernehmen" nehmen Sie alle auf einmal an.',
	'settings.secondOpinion.policy.protect': 'Nur maskieren · empfohlen',
	'settings.secondOpinion.policy.protect.text':
		'Erkennt die KI sicher einen Namen, eine Firma oder einen Ort, maskiert sie die Stelle selbst. Ob etwas sichtbar bleiben darf, entscheiden immer Sie.',
	'settings.secondOpinion.policy.protect_and_keep': 'Maskieren und stehen lassen',
	'settings.secondOpinion.policy.protect_and_keep.text':
		'Wie „Nur maskieren" – und zusätzlich lässt die KI gewöhnliche Wörter wie Fachbegriffe unmaskiert, wenn sie zu mindestens {percent} % sicher ist. Weniger Fragen, aber irrt sie sich, bleibt ein Name sichtbar.',
	'settings.secondOpinion.reviewerTitle': 'So arbeitet der Prüfer',
	'settings.secondOpinion.reviewer.1':
		'Die Erkennung findet Namen, Firmen und Orte. Was sie nur vermutet, wird zur Frage – etwa zwei großgeschriebene Wörter, die keiner Liste bekannt sind.',
	'settings.secondOpinion.reviewer.2':
		'Das Modell liest jede Frage mit etwas Text davor und danach und wählt eine von vier festen Antworten. Es schreibt keinen Text und sendet nichts – es läuft nur auf diesem Gerät.',
	'settings.secondOpinion.reviewer.3':
		'Was es nach Ihrer Einstellung selbst entscheidet, steht unter „Von der lokalen KI geklärt" und lässt sich mit einem Klick umkehren. Im Prüfbericht zählt es getrennt.',
	'settings.secondOpinion.reviewer.4':
		'Was offen bleibt, wird beim Speichern maskiert – im Zweifel für den Schutz.',

	'settings.models.total': {
		one: 'auf diesem Gerät · {n} Bestandteil',
		other: 'auf diesem Gerät · {n} Bestandteile'
	},

	// Load additional models (initial setup)
	'setup.pageTitle': 'Einrichten',
	'setup.eyebrow': 'Erster Start',
	'setup.title': 'Einmal einrichten – danach bleibt alles auf diesem Gerät.',
	'setup.text':
		'Die Erkennungsmodelle sind zu groß für den App-Download. Sie werden jetzt einmal geladen und auf Echtheit geprüft. Hochgeladen wird dabei nichts – keine Dokumente, keine Nutzungsdaten.',
	'setup.list.selection': 'Was soll auf dieses Gerät?',
	'setup.list.load': 'Wird geladen und geprüft',
	'setup.total': 'Gesamt {size}',
	'setup.done': '{done} von {total} fertig',
	'setup.checksum': 'Jede Datei wird per Prüfsumme kontrolliert – Abweichungen werden verworfen.',
	'setup.fromFile': 'Kein Internet erlaubt? Modellpaket aus Datei installieren',
	'setup.start': 'Herunterladen und prüfen · {size}',
	'setup.onlyFile': 'Laden aus dem Internet ist abgeschaltet',
	'setup.ready': 'Das Nötige ist da – Sie können schon loslegen.',
	'setup.ready.background': 'Die Texterkennung lädt im Hintergrund weiter.',
	'setup.ready.all': 'Alles geladen und geprüft.',
	'setup.loads': 'Wird geladen und geprüft …',
	'setup.loads.text':
		'Sie können loslegen, sobald Namenserkennung und Sprache geprüft sind. Ein Abbruch behält, was schon geladen ist.',
	'setup.cancel': 'Abbrechen',
	'setup.getStarted': 'Loslegen',
	'setup.later': 'Später einrichten',
	'setup.toTheSelection': 'Zur Auswahl',
	'setup.whatWasLoaded': 'Was genau geladen wird',
	'setup.network':
		'Danach ist die Netzverbindung wieder gesperrt. Neue Modelle gibt es nur, wenn Sie es auslösen.',

	'models.ner': 'Namenserkennung',
	'models.ner.text':
		'Findet Namen, Firmen und Orte – in allen Sprachen. GLiNER multi-PII, als ONNX.',
	'models.de': 'Deutsch',
	'models.de.text': 'Satzzerlegung und Wortstamm-Erkennung für deutsche Dokumente (spaCy).',
	'models.en': 'Englisch',
	'models.en.text': 'Satzzerlegung und Wortstamm-Erkennung für englische Dokumente (spaCy).',
	'models.language': 'Sprache',
	'models.language.text': 'Satzzerlegung und Wortstamm-Erkennung',
	'models.ocr': 'Texterkennung für Fotos und Scans',
	'models.ocr.text': 'Liest Text aus Bildern und gescannten PDFs (Tesseract, deu und eng).',
	'models.ocr.text.system':
		'Liest Text aus Bildern und gescannten PDFs mit Apple Vision. Gehört zu macOS, nichts zu laden.',
	'models.ocr.preparation': 'Texterkennung wird vorbereitet (einmalig)',
	'models.classifier': 'Zweitmeinung',
	'models.classifier.text':
		'Beurteilt unsichere Stellen: Name, Firma, Ort oder Begriff. Kleiner Klassifikator, als ONNX. Optional.',
	'models.needed': 'nötig',
	'models.required': 'benötigt',
	'models.line.installed': 'installiert',
	'models.line.started': 'angefangen · {percent} % von {size}',
	'models.line.skipped': 'übersprungen',
	'models.line.reviewed': 'geprüft · {size}',
	'models.line.percent': '{percent} % von {size}',
	'models.line.review': 'wird geprüft …',
	'models.line.error': 'verworfen',
	'models.line.waits': 'wartet · {size}',
	'models.state.installed': 'installiert',
	'models.state.missing': 'fehlt',
	'models.state.partial': 'angefangen',
	'models.state.queued': 'wartet',
	'models.state.downloading': 'lädt',
	'models.state.verifying': 'prüft',
	'models.state.installing': 'installiert …',
	'models.state.failed': 'fehlgeschlagen',
	'models.state.cancelled': 'abgebrochen',
	'models.source': 'Quelle',
	'models.source.lang': 'Woher Modelle geladen werden',
	'models.source.huggingface': 'Hugging Face (Standard)',
	'models.source.github': 'GitHub',
	'models.source.own': 'Eigener Server …',
	'models.source.file': 'Lokale Datei',
	'models.source.address': 'Adresse des eigenen Servers',
	'models.source.accept': 'Übernehmen',
	'models.source.bundle': 'mitgeliefert',
	'models.source.local': 'Entwicklungsumgebung',
	'models.source.system': 'im System enthalten',
	'models.network':
		'Die App öffnet eine Netzverbindung nur, während Sie einen Download starten – und nur zu dieser Quelle. Geladen werden ausschließlich Modelle; ihre Prüfsummen stehen fest in der signierten App. Weicht eine Datei ab, wird sie verworfen.',
	'models.checksums.notice':
		'Feste Revision und SHA-256 je Datei stehen in der signierten App. Nur ONNX und Modelldaten – kein Pickle, keine ausführbaren Formate.',
	'models.checksums.on': 'Liste mit Prüfsummen anzeigen',
	'models.checksums.from': 'Liste mit Prüfsummen ausblenden',
	'models.package': 'Modellpaket aus Datei installieren …',
	'models.package.dialog': 'Modellpaket auswählen',
	'models.package.filter': 'Modellpaket (ZIP)',
	'models.package.done': 'Modellpaket geprüft und installiert.',
	'models.notAvailable': 'Die Modelle lassen sich gerade nicht anzeigen.',
	'models.existing.withoutDownload': 'mit der App ausgeliefert oder lokal vorhanden',
	'models.existing.reviewed': 'alle per Prüfsumme bestätigt · zuletzt geprüft {date}',
	'models.existing.unreviewed': 'per Prüfsumme bestätigt',
	'models.review': 'Jetzt prüfen',
	'models.review.ok': 'Alle Modelle stimmen mit ihren Prüfsummen überein.',
	'models.review.deviation': {
		one: '{n} Modell wich von seiner Prüfsumme ab und wurde entfernt.',
		other: '{n} Modelle wichen von ihren Prüfsummen ab und wurden entfernt.'
	},
	'models.load': 'Laden',
	'models.continue': 'Fortsetzen',
	'models.remove': 'Entfernen',
	'models.removed': '{name} entfernt.',
	'models.remove.title': '{name} entfernen?',
	'models.remove.text': 'Die Dateien werden von diesem Gerät gelöscht.',
	'models.remove.sequence.setup':
		'Ohne diesen Bestandteil wird nichts mehr erkannt – Ablegen bleibt gesperrt, bis er wieder geladen ist.',
	'models.remove.sequence.loadNew':
		'Sie können ihn jederzeit wieder laden oder aus einer Paketdatei installieren.',
	'models.remove.ja': 'Entfernen',
	'models.error.general': 'Das hat gerade nicht geklappt.',
	'models.error.busy': 'Es läuft bereits ein Download oder eine Prüfung.',
	'models.error.onlyFile':
		'Laden aus dem Internet ist abgeschaltet. Wählen Sie eine Quelle oder installieren Sie ein Modellpaket.',
	'models.error.server':
		'Die Adresse des Servers ist ungültig. Erlaubt ist nur https:// ohne Zugangsdaten.',
	'models.error.host':
		'Die Quelle hat auf einen nicht erlaubten Server verwiesen. Nichts wurde geladen.',
	'models.error.network':
		'Die Verbindung zur Quelle brach ab. Ein neuer Versuch setzt dort fort, wo er aufgehört hat.',
	'models.error.source': 'Die Quelle hat diese Datei nicht. Versuchen Sie eine andere Quelle.',
	'models.error.checksum':
		'Eine Datei wich von ihrer Prüfsumme ab und wurde verworfen. Installiert wurde nichts davon.',
	'models.error.cancelled': 'Abgebrochen. Was schon geladen ist, bleibt für den nächsten Versuch.',
	'models.error.disk': 'Die Dateien ließen sich nicht speichern. Ist genug Platz frei?',
	'models.error.package': 'Das ist kein Modellpaket.',
	'models.error.packageIncomplete': 'Im Modellpaket fehlen Dateien.',
	'models.error.packageEmpty': 'Das Paket enthält keine Modelle für diese App-Version.',

	'settings.names.notice': 'Eine Liste ist älter als eine Woche',
	'settings.names.notAvailable': 'Namen & Begriffe lassen sich gerade nicht laden.',
	'settings.names.listen': 'Bekannte Namen',
	'settings.names.listen.text':
		'Werden sicher gefunden – auch dort, wo die automatische Erkennung scheitert, etwa in »Tischlerei Josef Brandstetter e.U.«.',
	'settings.names.people': 'Personen',
	'settings.names.companies': 'Firmen',
	'settings.names.num': { one: '{n} Name', other: '{n} Namen' },
	'settings.names.configured': 'Aus dem nächtlichen Export',
	'settings.names.current': 'aktuell',
	'settings.names.stale': 'bitte aktualisieren',
	'settings.names.no':
		'Noch keine Liste. Eine CSV-, JSON- oder Textdatei mit einem Namen je Zeile genügt.',
	'settings.names.encrypted': 'Verschlüsselt auf diesem Gerät gespeichert.',
	'settings.names.add': 'Liste hinzufügen',
	'settings.names.new.people': 'Personenliste …',
	'settings.names.new.companies': 'Firmenliste …',
	'settings.names.update': 'Jetzt aktualisieren',
	'settings.names.dialog': 'Namensliste wählen',
	'settings.names.dialog.update': '»{name}« erneut wählen',
	'settings.names.filter': 'Namensliste (CSV, JSON, Text)',
	'settings.names.added': 'Liste hinzugefügt.',
	'settings.names.updated': 'Listen aktualisiert.',
	'settings.names.remove': '{name} entfernen',
	'settings.names.remove.title': 'Liste entfernen?',
	'settings.names.remove.text':
		'Die Namen aus »{name}« werden nicht mehr gezielt gesucht. Die Datei selbst bleibt, wo sie ist.',
	'names.state.today': 'Stand heute, {time}',
	'names.state.yesterday': 'Stand gestern, {time}',
	'names.state.days': { one: 'Stand vor {n} Tag', other: 'Stand vor {n} Tagen' },

	'settings.kind': 'Art der Angabe',
	'settings.kind.PERSON': 'Name',
	'settings.kind.ORGANIZATION': 'Firma',
	'settings.kind.LOCATION': 'Ort',
	'settings.kind.CUSTOMER_NUMBER': 'Kundennummer',
	'settings.kind.CASE_NUMBER': 'Aktenzeichen',
	'settings.kind.OTHER': 'Sonstiges',
	'settings.placeholder.PERSON': '[Name]',
	'settings.placeholder.ORGANIZATION': '[Firma]',
	'settings.placeholder.LOCATION': '[Ort]',
	'settings.placeholder.CUSTOMER_NUMBER': '[Kundennummer]',
	'settings.placeholder.CASE_NUMBER': '[Aktenzeichen]',
	'settings.placeholder.OTHER': '[Angabe]',

	'settings.terms': 'Eigene Begriffe',
	'settings.terms.text':
		'Wörter, die immer maskiert werden sollen – etwa Projekt- oder Produktnamen.',
	'settings.terms.as': 'als {kind}',
	'settings.terms.remove': '{term} entfernen',
	'settings.terms.new': 'Neuer Begriff',
	'settings.terms.placeholder': 'Begriff hinzufügen …',
	'settings.terms.add': 'Hinzufügen',

	'settings.pattern': 'Eigene Muster',
	'settings.pattern.text':
		'Für Nummern mit festem Aufbau. Ein Muster, das fast jeden Text träfe, nimmt die App nicht an.',
	'settings.pattern.becomes': 'wird zu {placeholder}',
	'settings.pattern.use': '{name} verwenden',
	'settings.pattern.remove': '{name} entfernen',
	'settings.pattern.new': 'Muster hinzufügen',
	'settings.pattern.name': 'Name',
	'settings.pattern.expression': 'Muster (regulärer Ausdruck)',
	'settings.pattern.expression.notice': 'Zum Beispiel POL-\\d{6,8} für »POL-4471920«.',
	'settings.pattern.add': 'Muster hinzufügen',

	'settings.try': 'Ausprobieren',
	'settings.try.none': 'Kein Treffer.',
	'settings.try.match': {
		one: '{n} Treffer – so würde der Satz maskiert.',
		other: '{n} Treffer – so würde der Satz maskiert.'
	},
	'settings.try.draftInvalid':
		'Das neue Muster lässt sich so nicht verwenden – siehe Hinweis am Feld.',

	'settings.template': 'Vorlage',
	'settings.template.text':
		'Begriffe, Muster und Maskierungs-Standard gemeinsam speichern und im Team weitergeben. Namenslisten bleiben auf diesem Gerät.',
	'settings.template.without': 'Ohne Vorlage',
	'settings.template.example': '{title} (Beispiel)',
	'settings.template.save': 'Speichern als …',
	'settings.template.export': 'Exportieren',
	'settings.template.import': 'Importieren',
	'settings.template.save.title': 'Als Vorlage speichern',
	'settings.template.save.text':
		'Die aktuellen Begriffe, Muster und der Maskierungs-Standard werden unter diesem Namen gespeichert.',
	'settings.template.save.go': 'Speichern',
	'settings.template.name': 'Name der Vorlage',
	'settings.template.saved': 'Vorlage »{title}« gespeichert.',
	'settings.template.standardTitle': 'Meine Vorlage',
	'settings.template.export.dialog': 'Vorlage speichern',
	'settings.template.exported': 'Vorlage gespeichert.',
	'settings.template.import.dialog': 'Vorlage wählen',
	'settings.template.import.filter': 'unname-Vorlage (YAML)',
	'settings.template.imported': 'Vorlage eingelesen und gewählt.',
	'settings.template.replace.title': 'Vorlage wählen?',
	'settings.template.replace.text': 'Begriffe und Muster der Vorlage ersetzen die aktuellen.',
	'settings.template.replace.terms': {
		zero: 'Es gibt noch keine eigenen Begriffe.',
		one: '{n} eigener Begriff wird ersetzt.',
		other: '{n} eigene Begriffe werden ersetzt.'
	},
	'settings.template.replace.pattern': {
		zero: 'Es gibt noch keine eigenen Muster.',
		one: '{n} eigenes Muster wird ersetzt.',
		other: '{n} eigene Muster werden ersetzt.'
	},
	'settings.template.replace.listen': 'Namenslisten bleiben, wie sie sind.',
	'settings.template.replace.ja': 'Vorlage wählen',

	'settings.history.retention': 'Einträge im Verlauf automatisch löschen',
	'settings.history.days': { one: 'nach {n} Tag', other: 'nach {n} Tagen' },
	'settings.history.text':
		'Gelöscht wird der Eintrag mit seiner Dateiliste und den gespeicherten Kopien der Originale. Die Zuordnungen bleiben in der gemeinsamen Übersetzungstabelle – so lassen sich auch ältere KI-Antworten noch zurückübersetzen. Namen im Verlauf entstehen aus der Dokumentart, nie aus Personennamen.',
	'settings.history.clear': 'Verlauf leeren …',
	'settings.history.clear.title': 'Verlauf leeren?',
	'settings.history.clear.text': 'Alle Einträge verschwinden aus der Seitenleiste.',
	'settings.history.clear.sequence1': {
		one: '{n} Eintrag mit seiner Dateiliste und den gespeicherten Kopien der Originale wird gelöscht.',
		other:
			'{n} Einträge mit ihren Dateilisten und den gespeicherten Kopien der Originale werden gelöscht.'
	},
	'settings.history.clear.sequence2':
		'Gespeicherte Dateien in den Zielordnern bleiben unverändert.',
	'settings.history.clear.sequence3':
		'Die Übersetzungstabelle bleibt: KI-Antworten lassen sich weiter zurückübersetzen.',
	'settings.history.clear.ja': 'Verlauf leeren',
	'settings.history.cleared': 'Verlauf geleert.',
	'settings.history.space': 'Belegter Platz',
	'settings.history.space.value': {
		one: '{size} für {n} Eintrag',
		other: '{size} für {n} Einträge'
	},
	'settings.history.space.free': '{free} frei auf dem Laufwerk',
	'settings.history.space.text':
		'Jeder Eintrag hält eine verschlüsselte Kopie seiner Originale mit Auslesung und Erkennung, damit er sich jederzeit wieder öffnen lässt. Die Frist löscht auch die gespeicherten Originale; „Eintrag löschen" gibt den Platz sofort frei.',
	'settings.history.space.error': 'Der belegte Platz ließ sich gerade nicht ermitteln.',

	'settings.table.notAvailable': 'Die Übersetzungstabelle ist gerade nicht verfügbar.',
	'settings.table.mappings': {
		one: 'Zuordnung aus {entries} Einträgen',
		other: 'Zuordnungen aus {entries} Einträgen'
	},
	'settings.table.protected': 'verschlüsselt · nur auf diesem Gerät',
	'settings.table.protected.date': 'verschlüsselt · nur auf diesem Gerät · Schlüssel vom {date}',
	'settings.table.text':
		'Alles nutzt dieselbe Tabelle. Deshalb erkennt »Zurückübersetzen« Codes aus jedem Eintrag im Verlauf – und derselbe Name bekommt überall denselben Code.',
	'settings.table.retention': 'Ungenutzte Einträge löschen',
	'settings.table.months': { one: 'nach {n} Monat', other: 'nach {n} Monaten' },
	'settings.table.keep': 'Auf ein anderes Gerät mitnehmen',
	'settings.table.keep.text': 'Tabelle und Schlüssel als passwortgeschützte Sicherung',
	'settings.table.backup': 'Sicherung exportieren',
	'settings.table.import': 'Importieren',
	'settings.table.new': 'Neu beginnen',
	'settings.table.new.text':
		'Ein neuer Schlüssel oder eine leere Tabelle machen alle bisherigen Codes unlesbar.',
	'settings.table.key': 'Neuer Schlüssel …',
	'settings.table.clear': 'Tabelle leeren …',
	'settings.table.key.title': 'Neuen Schlüssel anlegen?',
	'settings.table.key.text': 'Der bisherige Schlüssel und die Tabelle werden gelöscht.',
	'settings.table.key.ja': 'Neuen Schlüssel anlegen',
	'settings.table.key.done': 'Neuer Schlüssel angelegt.',
	'settings.table.clear.title': 'Tabelle leeren?',
	'settings.table.clear.text': 'Alle Zuordnungen werden gelöscht; der Schlüssel bleibt.',
	'settings.table.clear.ja': 'Tabelle leeren',
	'settings.table.clear.done': 'Übersetzungstabelle geleert.',
	'settings.table.sequence.codes':
		'Codes in bisherigen Ausgaben und KI-Antworten lassen sich nicht mehr zurückübersetzen.',
	'settings.table.sequence.mappings': {
		zero: 'Die Tabelle ist schon leer.',
		one: '{n} Zuordnung geht verloren.',
		other: '{n} Zuordnungen gehen verloren.'
	},
	'settings.table.sequence.newCodes': 'Derselbe Name bekommt ab jetzt einen anderen Code.',
	'settings.table.sequence.sameCodes':
		'Derselbe Name bekommt weiter denselben Code – nur die Zuordnung ist weg.',
	'settings.table.backup.title': 'Sicherung exportieren',
	'settings.table.backup.text':
		'Die Sicherung enthält Tabelle und Schlüssel, also echte Namen. Ohne dieses Passwort lässt sie sich nicht öffnen – auch nicht von uns.',
	'settings.table.backup.go': 'Sicherung speichern …',
	'settings.table.backup.dialog': 'Sicherung speichern',
	'settings.table.backup.saved': 'Sicherung gespeichert.',
	'settings.table.import.title': 'Sicherung importieren',
	'settings.table.import.text':
		'Geben Sie das Passwort der Sicherung ein und wählen Sie dann die Datei.',
	'settings.table.import.sequence1':
		'Schlüssel und Tabelle dieses Geräts werden durch die Sicherung ersetzt.',
	'settings.table.import.sequence2': {
		one: '{n} Zuordnung, die nur auf diesem Gerät steht, geht verloren.',
		other: '{n} Zuordnungen, die nur auf diesem Gerät stehen, gehen verloren.'
	},
	'settings.table.import.go': 'Datei wählen und einlesen …',
	'settings.table.import.dialog': 'Sicherung wählen',
	'settings.table.import.filter': 'unname-Sicherung',
	'settings.table.import.done': {
		one: 'Sicherung eingelesen: {n} Zuordnung.',
		other: 'Sicherung eingelesen: {n} Zuordnungen.'
	},
	'settings.table.password': 'Passwort',
	'settings.table.password.notice': 'Mindestens {n} Zeichen.',
	'settings.table.password.repeat': 'Passwort wiederholen',
	'settings.table.password.unequal': 'Die beiden Passwörter sind verschieden.',

	'settings.technical.detection': 'Erkennung',
	'settings.technical.gliner': 'GLiNER multi-PII · alle Sprachen',
	'settings.technical.spacy': 'spaCy ({name}) · Rückfall, deutsch',
	'settings.technical.unknown': 'noch nicht gemeldet',
	'settings.technical.sentences': 'Satzzerlegung',
	'settings.technical.sentences.value': 'spaCy · de',
	'settings.technical.ocr': 'Texterkennung in Bildern',
	'settings.technical.ocr.value': 'Tesseract · deu, eng',
	'settings.technical.ocr.tesseract': 'Tesseract {version} · deu, eng',
	'settings.technical.ocr.vision': 'Apple Vision · Revision {revision} · macOS {os} ({build})',
	'settings.technical.ocr.vision.short': 'Apple Vision',
	'settings.technical.ocr.missing': 'nicht verfügbar',
	'settings.technical.local': 'Lokale Verarbeitung',
	'settings.technical.local.ready': 'bereit · nur auf diesem Gerät',
	'settings.technical.local.not': 'nicht bereit',
	'settings.technical.state.ready':
		'Version {version}, PID {pid}, Port {port}, nur über 127.0.0.1 erreichbar.',
	'settings.technical.state.ready.socket':
		'Version {version}, PID {pid}, ohne Port – nur über einen privaten Socket im App-Container erreichbar.',
	'settings.technical.state.restart': '{reason} Ein Neustart läuft.',
	'settings.technical.state.failure': '{reason} Es läuft kein weiterer automatischer Versuch.',
	'settings.technical.state.starting': 'Prozess gestartet, Handshake steht noch aus.',
	'settings.technical.state.stopped': 'Prozess bewusst beendet.',
	'settings.technical.basis': 'Grundlage',
	'settings.technical.basis.value': 'unname {version} · basiert auf Presidio (MIT)',
	'settings.technical.diagnostics': 'Diagnosebericht kopieren',
	'settings.technical.copied': 'Diagnosebericht kopiert – ohne Inhalte aus Dokumenten.',
	'settings.technical.developer': 'Entwicklermodus',
	'settings.technical.process': 'Prozess',
	'settings.technical.restarts': 'Neustarts',
	'settings.technical.service': 'Dienst',
	'settings.technical.device': 'Gerät',
	'settings.technical.worker': 'Dokument-Worker',

	'settings.error.general': 'Das ging gerade nicht. Bitte versuchen Sie es noch einmal.',
	'settings.error.noConnection':
		'Die lokale Verarbeitung antwortet gerade nicht – die Änderung wurde nicht gespeichert.',
	'settings.error.notSaved': 'Die Änderung ließ sich nicht speichern.',
	'settings.error.passwordShort': 'Das Passwort braucht mindestens 10 Zeichen.',
	'settings.error.passwordLong': 'Das Passwort ist zu lang.',
	'settings.error.passwordWrong':
		'Das Passwort passt nicht – oder die Datei wurde verändert. Nichts wurde ersetzt.',
	'settings.error.backupInvalid': 'Die Datei ist keine unname-Sicherung.',
	'settings.error.listFormat':
		'Die Datei braucht eine eindeutige Namensspalte (etwa »Name«) oder eine Liste von Namen.',
	'settings.error.listEmpty': 'In der Datei steht kein Name.',
	'settings.error.listKind': 'Namenslisten können CSV, JSON oder Text sein.',
	'settings.error.encoding': 'Die Datei ist kein UTF-8-Text.',
	'settings.error.tooLarge': 'Die Datei ist zu groß.',
	'settings.error.tooMany': 'Das sind zu viele Einträge.',
	'settings.error.unreadable': 'Die Datei ließ sich nicht lesen.',
	'settings.error.write': 'Die Datei ließ sich dort nicht speichern.',
	'settings.error.termShort': 'Ein Begriff braucht mindestens 3 Zeichen.',
	'settings.error.patternInvalid': 'Das ist kein gültiges Muster.',
	'settings.error.patternEmpty': 'Das Muster trifft auch eine leere Stelle.',
	'settings.error.patternSlow':
		'Das Muster ist zu aufwendig auszuwerten und könnte die Erkennung lahmlegen.',
	'settings.error.patternWide': 'Das Muster trifft fast jeden Text. Bitte enger fassen.',
	'settings.error.templateInvalid': 'Die Datei ist keine gültige unname-Vorlage.',
	'settings.error.templateVersion': 'Diese Vorlage stammt aus einer anderen Version.',
	'settings.error.templateExample': 'So heißt schon eine Beispielvorlage. Bitte anders benennen.',

	// Menu bar and notification
	'notice.done.title': 'unname',
	'notice.done.text': {
		one: '{n} Datei fertig geprüft',
		other: '{n} Dateien fertig geprüft'
	},
	'menu.about': 'Über unname',
	'menu.settings': 'Einstellungen …',
	'menu.services': 'Dienste',
	'menu.hide': 'unname ausblenden',
	'menu.hideOther': 'Andere ausblenden',
	'menu.showAll': 'Alle einblenden',
	'menu.finish': 'unname beenden',
	'menu.dropzone': 'Ablage',
	'menu.selectFiles': 'Dateien wählen …',
	'menu.selectFolder': 'Ordner wählen …',
	'menu.closeWindow': 'Fenster schließen',
	'menu.edit': 'Bearbeiten',
	'menu.revoke': 'Widerrufen',
	'menu.repeat': 'Wiederholen',
	'menu.cut': 'Ausschneiden',
	'menu.copy': 'Kopieren',
	'menu.insert': 'Einsetzen',
	'menu.selectAll': 'Alles auswählen',
	'menu.window': 'Fenster',
	'menu.inDock': 'Im Dock ablegen',
	'menu.zoom': 'Zoomen',
	'menu.fullscreen': 'Vollbild',
	'menu.help': 'Hilfe',
	'menu.showHelp': 'Hinweise anzeigen'
} as const satisfies Record<string, Message>;

export type MessageKey = keyof typeof de;
