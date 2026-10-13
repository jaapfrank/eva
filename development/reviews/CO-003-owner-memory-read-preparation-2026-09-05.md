# CO-003 — voorbereiding van eigen geheugenbronnen

Controle op bron `60602fc`, 5 september 2026. Alleen-lezen voorbereiding;
geen implementatie, migratie, modelcall of acceptatieclaim.

## Gevonden aansluiting die vóór bouwen opgelost moet zijn

- `TelegramCanaryBridge.principal_id` maakt altijd `telegram:<user-id>`.
  De gewone `/eva2`-API gebruikt deze admission en daarmee deze persoonscode.
- `PostgresHindsightMemoryRuntime.dashboard_turn` gebruikt daarentegen de
  geconfigureerde eigenaar-UUID. Dat is niet dezelfde broneigenaar.
- De alleen-lezen configuratiecontrole in de actieve slot3-image bevestigde:
  eigenaar en dashboardbinding ingesteld, beide passend bij één v2-Telegrambinding,
  nul bindings met een `telegram:<user-id>`-principal. Geen IDs, secrets of
  gespreksteksten zijn uitgelezen naar het verslag; geen databasequery gedaan.
- Een query die alleen de dashboard-UUID gebruikt, is daarom geen bewijs dat
  Jaap zijn bestaande gewone Telegramgeheugen kan bekijken. Of andere bronnen
  voor die UUID bestaan, is niet onderzocht; een leeg totaal wordt niet beweerd.
- `PostgresMemoryOutbox.pending` selecteert alleen nog te verwerken werk;
  afgeronde retentions ontbreken. Deze methode is geen geheugenlijst.
- De bestaande `dashboard_people_memory` levert uitsluitend grove statussen
  en roept Hindsight-health aan. Een bronlijst mag dat niet stil overnemen als
  zij zonder provider- of modelcall moet werken.

## Concrete gevallen voor de bestaande volgende kaart

1. Eén eigenaar heeft een canonieke privébron én een bestaande bridge-privébron;
   beide zijn zichtbaar uitsluitend na actuele server-side eigendomscontrole.
2. Een andere persoon, groep, ander tenant/installatie, ingetrokken binding of
   gewijzigde eigenaar maakt geen bron zichtbaar. Browserparameters mogen geen
   scope of alternatieve persoonscode toevoegen.
3. Afgeronde retentions zijn zichtbaar; gewiste, verlopen of groepsbronnen niet.
   Gebruik de bestaande gevalideerde outbox-documentparser en wisstatus, geen
   tweede kopie of reconstrueren van gewiste inhoud.
4. Paginering filtert bevoegdheid en zichtbaarheid vóór de paginalimiet;
   vreemde of gewiste rijen mogen geen lege eerste pagina met verborgen vervolg
   veroorzaken. Cursor en limiet blijven gesloten en begrensd.
5. Een nieuwe runtime leest dezelfde toegestane bronnen zonder modelcall,
   Hindsight-call of writes. HTTP blijft eigenaargebonden met `no-store`.

De implementatiekeuze voor de actuele, gecontroleerde eigenaarskoppeling is nog
open. Geen willekeurige alias-union, automatische identity-migratie of wijziging
van live data om de proef te laten slagen. De bestaande CO-003-bestanden blijven
de geplande scope; een noodzakelijke uitbreiding wordt vóór implementatie
expliciet beoordeeld. CO-002 blijft open voor menselijke gespreksbeoordeling.

Onafhankelijke broncontrole door `/root/identity_pair_review` bevestigde de
verschillende persoonscodes en de beperkte selectie van `pending()`. Dit is
GO voor deze voorbereiding, niet voor een gebouwde of live geheugenfunctie.
