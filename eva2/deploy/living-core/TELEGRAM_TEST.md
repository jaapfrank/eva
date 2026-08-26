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
4. Een gepinde Eva-testimage die deze repositoryversie bevat. Een image-tag
   zoals `latest` wordt geweigerd.
5. De vijf afzonderlijke testbestanden uit
   [`telegram-test.example`](telegram-test.example). De database-DSN moet
   exact deze vorm hebben, met een echt testwachtwoord:

 ```text
   postgresql+psycopg://postgres:<wachtwoord>@postgres-test:5432/eva2test
   ```

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

1. Start alleen een privégesprek met account A. De vaste diagnosezin bewijst
   uitsluitend transport; zij is nog geen Eva-gesprek.
2. Koppel account B via de normale koppelroute.
3. Voeg A, B en de testbot toe aan de lege testgroep. Test toestemming geven,
   een onschuldig groepsbericht en toestemming intrekken.
4. Herstart de testopstelling en controleer dat een ingetrokken toestemming
   ook daarna niet opnieuw toegang geeft.

Noteer per stap tijd, Telegram-account, chat, verwacht resultaat en werkelijk
resultaat. Sla geen token, wachtwoord of berichtinhoud in een review op.

## Eerlijke grens

Zolang deze proef niet werkelijk is uitgevoerd, is alleen de **testomgeving**
bewezen. Er is dan geen claim over echte Telegram-bezorging, Eva's gesprek,
gezinsprivacy in de praktijk of managed-B-deployment.
