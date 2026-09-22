# Algemene connectoren over privékanalen

Bron: fad87db7. Onafhankelijke bronreview door document_maintenance: GO op beide productmodules. De gedeelde actuele eigenaarresolver wordt hergebruikt; oorspronkelijke Telegramingress blijft behouden. Het connectorregister bewaart de gecontroleerde canonical eigenaar apart van die bron. Bestaande registraties en hashes blijven geldig.

Validatie tegen het verpakte image: 31 gerichte tests direct geslaagd. Twee testharnasproblemen opgelost (oude constructorstub en een uitsluitend voor de SQLite-fixture benodigde bibliotheek); beide betreffende tests vervolgens geslaagd. Geen productwijziging om tests te omzeilen. De nieuwe ketentest doorloopt factory, registry, discovery, gatewayadapter en durable wrapper met fake transport: gedeeld gebruik, replay zonder dubbele aanroep, andere persoon/groep/installatie en intrekking bij execute/observe.

Read-only kandidaatcontrole op de werkelijke database: beide privékanalen ontdekken dezelfde vier geregistreerde connectoren en dezelfde uitvoerbindingen. Geen externe toolactie, modelcall of bericht. Installatiebewijs en actuele livecontrole staan apart; dit is geen menselijke gebruiksacceptatie of bewijs voor elke andere providerroute.
