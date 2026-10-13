# Telegram projectupdate — onafhankelijke review

Datum: 12 september 2026. Reviewer: `telegram_delivery_review`.

**GO voor de begrensde lokale verzendhelper en één expliciet geautoriseerde
bevestigingssend.** Geen live verzending uitgevoerd door deze reviewer.
Deze beoordeling bewijst geen actieve uurplanning of gelezen bericht.

## Beoordeelde bron

- `tools/send_project_telegram_update.py`
- SHA-256: `458dfceca7fbaa8e8a3a7e54418b594abed47262567685a74232ae206ae5fde7`
- Hergebruik gecontroleerd in `_build_proactive_delivery`,
  `PostgresPrivateTelegramRecipient.current_chat_id` en
  `TelegramProactiveDelivery.deliver`.

De helper gebruikt de bestaande containeromgeving en gemounte secrets; er is
geen ontvangerargument. De bestaande resolver combineert een actuele private
binding uit het vertrouwde bindingsbestand met actieve, niet verlopen
persoons- en eigenaarbindingen in Postgres. Hij wordt opnieuw aangeroepen vlak
voor verzending. Een ingestelde alternatieve chat-ID moet exact overeenkomen.
De adapter controleert daarna privéchat, bericht-ID en exacte tekst in de
providerreceipt. Dit bewijst provideracceptatie, geen menselijke ontvangst.

## Onafhankelijke controles

Uitgevoerd met Linux Python en een tijdelijke lokale directory, zonder netwerk
of echte berichtinhoud:

- Dezelfde uurkey en payload leveren dezelfde registratie op met één dispatch;
  gewijzigde payload onder dezelfde key wordt geweigerd.
- Een gesimuleerde timeout blijft onzeker; herhaling dispatcht niet opnieuw.
  De fouttekst, waaronder een testsentinel voor geheime gegevens, komt niet in
  de registratie terecht.
- Een echt afzonderlijk proces eindigt met `os._exit(77)` tijdens de sender;
  de vooraf gecommitte claim blijft bestaan en voorkomt nieuwe dispatch.
- Zes gelijktijdige Linux-processen met dezelfde key doen samen precies één
  senderaanroep. Andere aanroepen mogen de nog onzekere claim terugkrijgen.
- State-directory heeft modus 0700, SQLite en atomisch gepubliceerde receipt
  modus 0600. Receipts bewaren hashes en status, geen tekst, bot-token of chat-ID.

Alle controles slaagden. Ook gelezen: projectdoel en relevante documentatie-
en onderhoudsingang. Geen volledige documentatie- of productaudit geclaimd.

## Grenzen en bediening

De garantie geldt per behouden lokaal register, uurkey en Linux-gebruiker.
De scheduler moet steeds dezelfde gebruiker en onveranderde UTF-8-messagefile
plus UTC-uurkey gebruiken. Het register is geen globale Telegram-deduplicatie.
Verwijderen of vervangen ervan, een andere Linux-gebruiker of een nieuwe key
vormt geen veilige retryroute. Een timeout of procesdood kan optreden nadat
Telegram al accepteerde; automatische herverzending blijft daarom uit.

Een gelijktijdige aanroep of een crash kan een onzekere receipt achterlaten;
de SQLite-registratie blijft de autoriteit. De beheerder kan dezelfde
ongewijzigde aanroep gebruiken om de huidige registratie opnieuw te exporteren.
Een bestaande schrijfbare receipt-directory voorkomt een exportfout na send.
Gebruik een apart receiptbestand, nooit het registerbestand zelf.

De live container moet de beoordeelde bestaande runtimewiring aanbieden.
De reviewer heeft geen secrets of echte chatbinding gelezen en geen container
herstart. De live uitvoerder controleert de echte route met uitsluitend de
ene geautoriseerde bevestiging. Er is geen nieuwe bot of scheduler toegevoegd;
de bestaande Codex-heartbeat moet afzonderlijk deze helper gaan aanroepen.
