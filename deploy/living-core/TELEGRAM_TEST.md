# Telegram testen zonder Eva A of B te raken

Deze route is een **aparte proefopstelling**. Zij gebruikt een nieuwe bot,
nieuwe database, nieuw volume en nieuwe Docker-netwerken. Gebruik nooit de
bestaande Eva-bot, gezinsgroep, B-secrets of productiedatabase.

## Wat je nodig hebt

1. Een nieuwe BotFather-testbot met een apart token.
2. Twee Telegram-accounts met twee verschillende telefoonnummers. Twee fysieke
   telefoons zijn handig, maar account B mag ook in Telegram Desktop/Web.
3. Eén lege testgroep met alleen A, B en de testbot.
4. Het numerieke Telegram-gebruikers-ID van A en, voor de gezinsproef, B. Dit
   is **niet** het telefoonnummer of de gebruikersnaam.

Er zijn twee verschillende proeven. Kies bewust welke je uitvoert:

| Wat wil je weten? | Accounts | Betekenis |
|---|---|---|
| Kan één persoon een tweede Telegram-account veilig koppelen? | A en B zijn allebei van jou | B krijgt via `/link` dezelfde persoon als A. Dit is **geen** gezins- of privacyproef. |
| Blijven twee gezinsleden en hun groepstoestemming apart? | A en B zijn verschillende personen | A en B krijgen vóór de start ieder hun eigen testpersoon. Alleen zo is een groepsproef echt meerpersoons. |

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
PYTHONPATH=src:. python3 src/tools/build_telegram_test_image.py <exacte-40-teken-gitcommit>
```

Kopieer uitsluitend de `image`-waarde uit de JSON-receipt naar de private
testconfiguratie. De builder weigert een andere dependency-lock, een ontbrekende
lokale parent-image, een netwerkbuild en een Dockerfile met package-installatie.

## Veilige proefvolgorde: twee gezinsleden en een groep

1. Maak in BotFather een nieuwe testbot. Schakel voor **alleen deze bot**
   groepsprivacy uit via BotFather `/setprivacy`, anders ziet zij gewone
   groepsberichten niet. Gebruik nooit de token van Eva A/B.
2. Maak de lege testgroep, voeg A, B en de testbot toe en stuur vanuit A één
   unieke marker, bijvoorbeeld `EVA2-TEST-GROUP-3fj7Qz8Nw2Ka`. De marker is
   geen wachtwoord en mag uitsluitend in deze lege groep staan.
3. Stuur eerst vanuit A én B in een **privéchat** met de nieuwe testbot
   `/start`. Haal daarna hun numerieke ID's op zonder een derde-partijbot of
   zichtbare token. Deze opdracht start Eva niet en schrijft niets:

   ```bash
   cd /home/administrator/project/Bottie-candidates/v2/eva2
   PYTHONPATH=src:. python3 src/tools/discover_telegram_test_accounts.py
   ```

   Plak de nieuwe BotFather-testtoken alleen bij de verborgen prompt. De twee
   getoonde positieve nummers zijn `<NUMERIEK_ID_VAN_ACCOUNT_A>` en
   `<NUMERIEK_ID_VAN_ACCOUNT_B>` in de volgende stap. Zie je er meer dan twee,
   maak dan een nieuwe testbot in plaats van bestaande privéupdates te raden.
4. Maak de testconfiguratie op de Docker-host. De opdracht vraagt de token
   onzichtbaar op, maakt sterke testwachtwoorden en vertrouwt A en B als twee
   verschillende testpersonen. Plak de token niet in Telegram, een
   shellgeschiedenis of een document.

   ```bash
   cd /home/administrator/project/Bottie-candidates/v2/eva2
   sudo env PYTHONPATH=src:. python3 src/tools/prepare_telegram_test_config.py \
     --owner-telegram-user-id <NUMERIEK_ID_VAN_ACCOUNT_A> \
     --owner-principal-id test:persoon-a \
     --member <NUMERIEK_ID_VAN_ACCOUNT_B>=test:persoon-b
   ```

   De output bevat alleen een veilig vervolgcommando; geen token of wachtwoord.
   De opdracht weigert een bestaande `/run/eva2-test` te overschrijven en
   start geen container of Telegram-verbinding.
5. Laat de voorbereidingsopdracht één keer veilig de exacte groeps-ID uit die
   marker lezen en voeg de groep alleen voor A en B toe. De commandoregel
   drukt noch de BotFather-token noch de markerinhoud terug. Dit start nog
   geen container of Telegram-poller.

   ```bash
   sudo env PYTHONPATH=src:. python3 src/tools/admit_telegram_test_group.py \
     --marker EVA2-TEST-GROUP-3fj7Qz8Nw2Ka \
     --grant-to <NUMERIEK_ID_VAN_ACCOUNT_A> \
     --grant-to <NUMERIEK_ID_VAN_ACCOUNT_B>
   ```

6. Start daarna uitsluitend de afgescheiden teststack:

   ```bash
   cd /home/administrator/project/Bottie-candidates/v2/eva2
   sudo env PYTHONPATH=src:. python3 -m tools.telegram_test_launch \
     --env-file /run/eva2-test/telegram-test.env
   ```

   De launcher weigert afwijkende images, productiepaden, volumes en secrets.
   Hij mag pas een Telegram-poller starten nadat de eigen lege database en
   diagnosecore gezond zijn.
7. Start privégesprekken met A én B. Ieder ontvangt de vaste diagnosezin:
   `Telegram-test ontvangen. Dit controleert de verbinding, niet Eva's gesprek.`
   Daarmee is alleen de echte Telegram-transportroute bewezen.
8. Stuur in de groep eerst één onschuldig bericht vanuit A en één vanuit B.
   Zonder toestemming mag de bot op beide **niet** reageren.
9. Stuur vanuit A's privéchat `/share <GROEPS_ID>`. Stuur daarna vanuit A één
   groepsbericht: de vaste diagnosezin moet alleen daarop volgen. Een bericht
   van B blijft nog stil.
10. Herhaal `/share <GROEPS_ID>` vanuit B's privéchat. Nu mag B's groepsbericht
   ook de vaste diagnosezin krijgen.
11. Stuur vanuit A `/stop-sharing <GROEPS_ID>` en stuur weer vanuit A in de
    groep. Eva moet stil blijven voor A, ook na een herstart van de teststack;
    B mag alleen blijven werken zolang B's eigen toestemming nog actief is.
12. Laat de draaiende teststack daarna zelf een **gesaneerde receipt** maken.
    Kies een nieuwe bestandsnaam; de opdracht weigert stil overschrijven en
    bewaart geen token, naam, account-ID, chat-ID, berichttekst of
    Telegram-provider-ID.

    ```bash
    cd /home/administrator/project/Bottie-candidates/v2/eva2
    sudo env PYTHONPATH=src:. python3 src/tools/capture_telegram_test_receipt.py \
      --output /var/tmp/eva2-telegram-native-test-receipt-2026-08-26.json
    ```

    De receipt telt alleen Eva's eigen ledgeruitkomsten: toegelaten,
    geweigerde, voorbereide, afgeleverde en onduidelijke Telegram-updates. Hij
    controleert vooraf opnieuw dat uitsluitend de afgescheiden testcontainers,
    netwerken, database en image actief zijn. Een `delivered`-telling bewijst
    Telegrams provideracceptatie; vergelijk daarnaast zelf in de twee chats
    dat A en B werkelijk de juiste zichtbare antwoorden kregen. De receipt is
    geen bewijs van een volledig Eva-gesprek of van managed-B.

De diagnosecore bewaart geen echte herinnering en voert geen echte Eva-taak
uit. Deze proef bewijst dus Telegram-bezorging, accounttoelating en de
groepsconsentgrens; zij bewijst nog geen volledige Eva-conversatie.

## Daarna pas: dezelfde testbot met de echte gesprekcore

De echte gesprekcore is een afzonderlijke overlay. Kopieer haar modeltoegang
of identiteit niet met de hand. De voorbereider leest één access-only
modelbestand, één access-only Hindsight-bestand en de ondertekende identiteit,
weigert ieder refresh-token en maakt één private snapshot onder
`/run/eva2-test/conversation-secrets`. Beide images moeten op een exacte
SHA-256-digest staan. De opdracht start niets en toont geen tokenwaarde:

```bash
sudo env PYTHONPATH=src:<PAD_NAAR_DE_GEPINDE_RUNTIMEPACKAGES> \
  python3 src/tools/prepare_telegram_conversation_test.py \
  --model-access-file <ACCESS_ONLY_MODELFILE> \
  --hindsight-access-file <ACCESS_ONLY_HINDSIGHT_AUTH_JSON> \
  --identity-file <ONDERTEKEND_IDENTITYPACK> \
  --identity-trust-file <IDENTITY_TRUSTBINDING> \
  --core-image <EXACTE_EVA2_IMAGE_DIGEST> \
  --hindsight-image <EXACTE_HINDSIGHT_IMAGE_DIGEST>
```

Render daarna de support-, ingress- en gesprekmanifesten samen met zowel
`/run/eva2-test/telegram-test.env` als
`/run/eva2-test/telegram-conversation-test.env`. Een functioneel groene proef
is nog geen release: beide exacte gesprekimages moeten daarnaast groen zijn in
de netwerkloze kwetsbaarheidspoort.

## Afzonderlijke proef: twee accounts van één persoon

Laat `--member` en de groepstappen weg wanneer B jouw tweede eigen account is.
Start de teststack met alleen A. Stuur vanuit B in een privéchat `/link`; B
krijgt een korte code. Stuur die code vanuit A's privéchat als
`/link EVA-...`. A krijgt `Gekoppeld.`. Daarna kan B privé dezelfde vaste
diagnosezin krijgen. B wordt daarmee **geen tweede gezinslid** en krijgt geen
automatisch groepsrecht.

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
