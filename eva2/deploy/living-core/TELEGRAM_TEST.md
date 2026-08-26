# Telegram testen zonder Eva A of B te raken

Deze route is een **aparte proefopstelling**. Zij gebruikt een nieuwe bot,
nieuwe database, nieuw volume en nieuwe Docker-netwerken. Gebruik nooit de
bestaande Eva-bot, gezinsgroep, B-secrets of productiedatabase.

## Wat je nodig hebt

1. Een nieuwe BotFather-testbot met een apart token.
2. Twee Telegram-accounts met twee verschillende telefoonnummers:
   account A is de eigenaar, account B is het tweede gezinslid. Twee fysieke
   telefoons zijn handig, maar Telegram kan ook twee accounts op één toestel
   gebruiken.
3. Eén lege testgroep met alleen A, B en de testbot.
4. Het numerieke Telegram-gebruikers-ID van account A. Dit is **niet** het
   telefoonnummer en ook niet de gebruikersnaam. Haal het bijvoorbeeld op met
   een bestaande, betrouwbare Telegram-ID-bot en noteer alleen het nummer.

Je maakt geen losse DSN, wachtwoorden, JSON-bestanden, volumes of
netwerknamen. De voorbereidingsopdracht hieronder maakt die zelf, uitsluitend
in `/run/eva2-test` en alleen voor deze wegwerpproef.

## De testimage staat lokaal klaar

Voor commit `e11c136ada56367b31fee318dcda2b26641f41d8` is de testimage al
lokaal gebouwd en gecontroleerd:

```text
eva2-telegram-test@sha256:12cd4341765796f92c1279ba79017d9b164fed5510ef9d11baceb87e0dc4c621
```

Zet precies deze waarde in `EVA_TEST_LIVING_CORE_IMAGE` in je private
testconfiguratie. De image is alleen gemaakt uit die vaste Git-commit en een
lokaal aanwezige, gepinde eerdere Eva-image met dezelfde dependency-lock. Er
zijn tijdens de build geen packages gedownload of geïnstalleerd. Dit is een
wegwerpimage voor de Telegramproef, geen nieuwe productie- of B-image.

Bij een volgende bronwijziging bouw je opnieuw, zonder de vuile werkmap mee te
nemen:

```bash
PYTHONPATH=src:. python3 tools/build_telegram_test_image.py <exacte-40-teken-gitcommit>
```

Kopieer uitsluitend de `image`-waarde uit de JSON-receipt naar de private
testconfiguratie. De builder weigert een andere dependency-lock, een ontbrekende
lokale parent-image, een netwerkbuild en een Dockerfile met package-installatie.

## Veilige proefvolgorde

1. Maak in BotFather een nieuwe testbot. Gebruik nooit de token van Eva A/B.
2. Maak de testconfiguratie op de Docker-host. De opdracht vraagt de token
   onzichtbaar op, maakt sterke testwachtwoorden en vertrouwt aanvankelijk
   alleen account A. Plak de token niet in Telegram, een shellgeschiedenis of
   een document.

   ```bash
   cd /home/administrator/project/Bottie-candidates/v2/eva2
   sudo env PYTHONPATH=src:. python3 tools/prepare_telegram_test_config.py \
     --owner-telegram-user-id <NUMERIEK_ID_VAN_ACCOUNT_A>
   ```

   De output bevat alleen een veilig vervolgcommando; geen token of wachtwoord.
   De opdracht weigert een bestaande `/run/eva2-test` te overschrijven en
   start geen container of Telegram-verbinding.
3. Start daarna uitsluitend de afgescheiden teststack:

   ```bash
   cd /home/administrator/project/Bottie-candidates/v2/eva2
   sudo env PYTHONPATH=src:. python3 -m eva.operations.telegram_test_launch \
     --env-file /run/eva2-test/telegram-test.env
   ```

   De launcher weigert afwijkende images, productiepaden, volumes en secrets.
   Hij mag pas een Telegram-poller starten nadat de eigen lege database en
   diagnosecore gezond zijn.
4. Start een privégesprek met account A. De vaste diagnosezin bewijst alleen
   Telegram-transport; zij is nog geen Eva-gesprek.
5. Koppel account B via de normale koppelroute.
6. Voeg A, B en de testbot toe aan de lege testgroep. Test toestemming geven,
   een onschuldig groepsbericht en toestemming intrekken.
7. Herstart de testopstelling en controleer dat een ingetrokken toestemming
   ook daarna niet opnieuw toegang geeft.

## Voor je de launcher uitvoert

De launcher gebruikt uitsluitend de vastgezette Eva-testimage hierboven en de
vastgezette PostgreSQL-testinput uit de gegenereerde configuratie. Beide moeten
lokaal voor Docker beschikbaar zijn. De launcher trekt nooit stil `latest` of
een willekeurige PostgreSQL-tag binnen. Ontbreekt de exact gepinde
PostgreSQL-input, dan stopt de launch vóór een testbericht; laad dan eerst die
goedgekeurde input opnieuw in plaats van een tag te vervangen.

Noteer per stap tijd, Telegram-account, chat, verwacht resultaat en werkelijk
resultaat. Sla geen token, wachtwoord of berichtinhoud in een review op.

## Eerlijke grens

Zolang deze proef niet werkelijk is uitgevoerd, is alleen de **testomgeving**
bewezen. Er is dan geen claim over echte Telegram-bezorging, Eva's gesprek,
gezinsprivacy in de praktijk of managed-B-deployment.
