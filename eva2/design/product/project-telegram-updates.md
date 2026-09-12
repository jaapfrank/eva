# Gebundelde projectupdates naar Jaaps privé-Telegram

Eigenaarbesluit 12 september 2026: één gebundelde projectupdate per uur via de
bestaande privéroute. De coördinator gebruikt de bestaande Codex-heartbeat;
deze helper maakt geen scheduler, bot, modelcall of Eva-initiatief aan.

## Gebruik

Voer steeds uit als dezelfde Linux-gebruiker `administrator` in WSL Ubuntu.
Schrijf de bundel als UTF-8-bestand. Houd het bericht onder 4096 UTF-16-eenheden.
Gebruik een UTC-uursleutel; bewaar dezelfde sleutel én dezelfde inhoud bij een
herhaalde aanroep. Een andere inhoud bij dezelfde sleutel wordt geweigerd.

```sh
python3 /home/administrator/project/Eva2/eva2/tools/send_project_telegram_update.py \
  --message-file /home/administrator/.local/state/eva2/project-telegram-updates/update.txt \
  --hour-key 2026-09-12T15:00Z \
  --receipt /home/administrator/.local/state/eva2/project-telegram-updates/receipt.json
```

Gebruik uiteraard het bedoelde actuele UTC-uur. De oudermap van het bericht en
receipt moet bestaan. De helper bewaart zijn register altijd onder
`~/.local/state/eva2/project-telegram-updates/delivery.sqlite3`, mapmodus 0700 en
bestandsmodus 0600. Verwijder of vervang dit register niet om opnieuw te sturen.
Na een toekomstige slotwisseling geeft de beheerder de actuele kerncontainer mee
via `--container`; standaard is dit `eva2-managed-b-slot3-core`.

Exit 0 betekent: de bestaande adapter heeft een overeenkomstig Telegram-antwoord
ontvangen en het ontvangstbewijs is duurzaam vastgelegd. Dit bewijst geen lezen
of menselijke acceptatie. Een herhaalde aanroep met dezelfde inhoud geeft het
opgeslagen resultaat en verstuurt geen nieuw bericht. Exit 2 betekent: niet
bevestigd; inspecteer het lokale register en gebruik geen nieuwe sleutel om de
onzekerheid te omzeilen. `claimed_uncertain` en `uncertain` worden nooit automatisch
herhaald, ook niet na een crash of timeout. Bij een crash na verzending maar vóór
opslag kan daarom één update ontbreken in de bevestigde registratie. Zonder
onafhankelijk bewijs is opnieuw sturen verboden. Een volgende reguliere
uurupdate krijgt zijn eigen sleutel; neem daarin geen onzekere verzending als
bewezen op.

## Hergebruik en gegevensgrens

De helper gebruikt live `RuntimeSettings`, `_build_proactive_delivery` en
`TelegramProactiveDelivery`. Die route leest de bestaande gemonteerde secret en
controleert bij iedere verzending de actuele PostgreSQL-eigenaarsbevoegdheid plus
de vertrouwde privébinding. Er is geen ontvangerparameter. Groepen en ongeldige
of verlopen bindings worden geweigerd. Het adapterantwoord moet exact dezelfde
tekst, bedoelde chat en type `private` bevatten.

De bestaande expression-outbox in `sensory_events.py` selecteert uitsluitend
`sensory:home-assistant:`-gebeurtenissen en vraagt een sensory-besluit. Een
beheerupdate daarin stoppen zou een niet-bestaande huisgebeurtenis fabriceren.
Daarom bewaakt alleen een klein SQLite-register de eenmalige beheerdispatch;
transport, bevoegdheden en providercontrole worden volledig hergebruikt.
Dit is geen tweede algemene berichtendienst of nieuwe dependency.

Token, chat-ID en rauwe providerreceipt verlaten de container niet. Het lokale
register en export bevatten alleen uursleutel, berichthash, tijden, status en
SHA-256 van de gevalideerde providerreceipt. Foutdetails/tracebacks worden niet
gepubliceerd omdat die secret-URL's kunnen bevatten. Het berichtbestand zelf is
privé beheerdata en hoort niet in Git of het openbare dashboard.

## Verificatie en beheer

`python3 tools/test_project_telegram_update.py` controleert met echte tijdelijke
SQLite-bestanden: deduplicatie, afwijkende inhoud, gelijktijdige aanroep, crash,
timeout na mogelijke acceptatie, herstel van receipt-export en invoergrenzen.
De tests versturen geen Telegramberichten. Onafhankelijke review gaat vooraf aan
eenmalige, expliciet toegestane bevestiging via de geverifieerde privéroute.

Beheerder: projectcoördinator. Controleer deze kleine koppeling bij wijziging van
runtime-wiring, Telegram-adapter of slotcontainer, en bij de eerstvolgende
onderhoudsronde. Uitzetten: verwijder de helperaanroep uit de bestaande heartbeat;
het register behouden. De Eva-runtime wordt voor deze helper niet gewijzigd of
herstart. Een werkende helper bewijst niet dat de heartbeat al is aangesloten.
