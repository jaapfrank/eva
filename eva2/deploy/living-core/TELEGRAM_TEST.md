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
