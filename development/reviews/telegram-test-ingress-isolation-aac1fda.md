# Review — afgescheiden Telegram-testroute (`aac1fda`)

Datum: 26 augustus 2026
Reviewer: onafhankelijk, na drie reviewrondes
Besluit: **GO voor testvoorbereiding; geen live- of B-claim**

## Wat nu veilig is voorbereid

De enige ondersteunde startopdracht leest eerst de testinstellingen, controleert
de paden en de testcore, en start daarna uitsluitend de losstaande
`telegram-ingress-test`-container. Bij één afwijking start Docker niet.

- vier afzonderlijke, private testbestanden onder
  `/run/eva2-test/secrets`; directe productiepadnamen, `..`, symlinks,
  hardlinks en dubbelgebruikte bestanden worden geweigerd;
- een vaste image-digest, aparte tenant en installatie-ID en één eigen Docker
  bridge-netwerk met precies één gelabelde testcore;
- de core moet op dat netwerk werkelijk de alias `eva-test-core` hebben;
- Compose krijgt een nieuwe private `0600`-snapshot van de al gecontroleerde
  instellingen, niet het later wijzigbare bronbestand;
- de container zelf weigert de gewone `core`-route en een andere Telegram-API.

## Controle

Op exact checkpoint `aac1fda` waren alle volgende controles groen:

```text
ruff check …
python -m py_compile …
PYTHONPATH=src:. python -m pytest -q -s \
  tests/bootstrap/test_telegram_test_ingress_deployment.py
# 12 passed
docker compose --env-file deploy/living-core/telegram-test.example \
  -f deploy/living-core/telegram-test-ingress.compose.yml config
```

De twaalfde test maakt bovendien echt een tijdelijk Docker-bridge-netwerk en
een gelabelde nep-core, controleert de werkelijke Docker-aliasstructuur en
ruimt container plus netwerk weer op. Er bleven geen tijdelijke Dockerbronnen
achter. Een gewone Pytest-run zonder `-s` werd in deze gedeelde werkruimte soms
door een verdwenen capture-tempfile onderbroken; dat is geen geslaagde of
mislukte producttest. De volledige `-s`-run hierboven is de geldige receipt.

## Wat dit nadrukkelijk nog niet bewijst

- Geen echte BotFather-bot, Telegram-account, groep, database of B-run is
  gestart.
- Bestandsmetadata bewijst niet dat de inhoud een andere database, bot-token
  of bindingset dan A/B bevat.
- De testcore is niet gecontroleerd op zijn eigen mounts, database of extra
  netwerken.
- Een bridge met echte Telegram-egress is geen algemene host- of
  internetisolatie.
- Er is nog geen post-start healthcheck, readback, cleanupflow of
  gebruikersproef.
- Een root-operator kan bewust de ondersteunde launcher omzeilen met ruwe
  Docker-commando's; dat valt buiten deze gebruikersgerichte veiligheidsgrens.

## Volgende echte stap

Maak een aparte testcore en testdatabase op het gelabelde netwerk, maak een
nieuwe BotFather-testbot en vul de vier nieuwe testbestanden. Daarna kan de
eigenaar twee Telegram-accounts en één lege testgroep gebruiken voor de
private, koppel- en groepsconsentproef. Pas die proef levert een Telegram-live
receipt op.
