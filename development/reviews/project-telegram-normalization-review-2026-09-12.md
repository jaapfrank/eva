# Project-Telegram: normalisatie en veilige diagnostiek

Datum: 12 september 2026. Onafhankelijke reviewer: telegram_delivery_review.

**GO voor deze begrensde helperwijziging. Geen herverzending van de onzekere
uurupdate van 21:00Z.** De volgende reguliere uurupdate mag afzonderlijk worden
verstuurd, met eigen actuele inhoud; dit is geen toestemming voor een retry van
het oude bericht onder een nieuwe key. Geen live netwerk- of Telegramcalls door
de reviewer uitgevoerd.

## Bronbinding

- tools/send_project_telegram_update.py SHA-256:
  `45ecfa2099c1e0cde64cb7379735c8bc60c75b85f1cbe88dae536f63d04ca4b1`
- tools/test_project_telegram_update.py SHA-256:
  `44a77ef2cec882c71eb957138a961b5ba2ed40a763a58daf56fecf279adcbfc6`

Beide bestanden gelezen. De uitvoerder rapporteerde negen offline tests; deze
review herhaalt die suite niet. De reviewer voerde aanvullende gerichte controles
uit met echte tijdelijke SQLite-registers en een netwerkluwe senderstub.

## Bevindingen en onafhankelijk bewijs

Geen blocker gevonden. De claimkey en hash blijven gebaseerd op de oorspronkelijke
gelezen tekst, terwijl alleen ASCII-spaties, tabs, CR en LF aan de randen uit de
wiretekst verdwijnen. Interne tekst en Unicode worden niet herschreven.
`sent_message_sha256` identificeert de bedoelde wiretekst: bij een onzekere status
is deze hash op zichzelf geen verzendbewijs.

Onafhankelijke controles geslaagd:

- Bestaande records met elk van de statussen uncertain, claimed_uncertain en
  delivered worden ongewijzigd teruggegeven, zonder senderaanroep.
- Andere oorspronkelijke tekst die dezelfde genormaliseerde wiretekst oplevert,
  conflicteert onder dezelfde uurkey; normalisatie omzeilt deduplicatie niet.
- Interne lege regels, inspringing en een niet-ASCII NBSP blijven behouden;
  dubbele aanroep blijft één dispatch.
- Diagnostiek accepteert uitsluitend vaste fasen, echte booleans en geldige
  SHA-256-strings. Verkeerde typen, chat-ID en providertekst worden uitgesloten.

De bestaande duurzame claim wordt nog steeds vóór de sender gecommit; de
transactie/concurrency-route is niet veranderd. Een onzekere uitkomst houdt de
claim en krijgt geen automatische herverzending. Eerdere procesdood- en
concurrencyreview staat in telegram-project-update-review-2026-09-12.md.

De observatiewrapper geeft de oorspronkelijke providerresponse ongewijzigd aan
de bestaande Telegramadapter terug. Hij vervangt of versoepelt de controle op
exacte tekst, positieve bericht-ID en bedoelde privéchat niet. Een waargenomen
providerreceipt-hash bij een tekstmismatch is diagnostiek, geen delivered-status.
De bestaande actuele eigenaarbinding en gemounte secrets blijven de enige route;
geen ontvangerargument, nieuwe bot, scheduler of runtimecutover toegevoegd.

## Bewijsgrens

De ontbrekende providerdiagnostiek van de oude 21:00Z-poging kan hiermee niet
worden teruggewonnen. De afsluitende newline is een mogelijke verklaring, geen
vastgestelde oorzaak van die onzekere poging. De reviewer heeft geen echte
binding, secret of providerantwoord opgehaald. De uitvoerder verzorgt afzonderlijk
de offline echte-adaptercontrole en documentatie; toekomstige reguliere ontvangst
is nog geen bewijs dat het oude bericht wel of niet bezorgd werd.
