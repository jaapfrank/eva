# Reproduceerbare fotobibliotheek

Dit recept bouwt de bestaande passieve foto-providerbibliotheek met dotenv 18.0.9,
better-sqlite3 13.0.3 en node-addon-api 8.9.2. De elf bestaande bronbestanden
staan met hun exacte hashes in source-files.json. Lever die bron-snapshot
expliciet aan. Elk bronbestand wordt bytegelijk overgenomen. De vendorlicentiebestanden
staan in de geverifieerde npm-bomen.

Gebruik Linux x64 met de geselecteerde Node 22.23.4 en npm 10.9.8.

    python3 prepare.py --source-snapshot /pad/naar/bron-snapshot --output /pad/naar/nieuw-bouwresultaat --node /pad/naar/node --npm-cli /pad/naar/npm-cli.js

De builder controleert alle bronhashes vóór de bouw, kopieert uitsluitend de
elf aangewezen bestanden en installeert de vastgezette npm-closure met
uitgeschakelde installatiescripts. Hij controleert alle vendorbytes, modi en
linkdoelen tegen vendor-files.json en verifieert npm-handtekeningen. Het
resultaat staat in photo-library-candidate, samen met reproduction.json.
De opgenomen npm-cachemetadata wordt bytegelijk hersteld na vergelijking met
de volledige gegenereerde cache. De bekende npm-ci-variant voegt uitsluitend
het installatiescriptkenmerk van better-sqlite3 toe. De vendorscripts blijven
uitgeschakeld tijdens de bouw.
De bestaande configuratie en profielgegevens blijven invoer van de runtime.

De productieoverdracht selecteert src/brain/llm.js via de bestaande
EVA_TELEGRAM_PHOTO_PROVIDER_MODULE. Publicatie van de gesloten prefix vereist
bestandssync, directorysync van onder naar boven en een bewaakte selectie op
de bestaande Telegram-service. De oorspronkelijke bibliotheek en dezelfde
actuele database vormen de voorbereide terugweg. De geregistreerde
onderhoudsoplevering bevat de precieze installatie- en reviewhashes.

De onderhoudsproeven omvatten module-resolutie, native SQLite, privé synthetische
DB-schrijfacties en behoud van de bestaande profiel- en transportgegevens.
Eigenaargebruik van de fotofunctie is in deze ronde onbeproefd.
