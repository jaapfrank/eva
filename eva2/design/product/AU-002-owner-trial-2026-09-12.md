# Hulpagent proberen via Telegram

De start/stop/hervatketen is technisch bewezen via de interne Telegrambrug.
Deze proef controleert jouw normale Telegramervaring; jouw oordeel blijft open.
De verbeterde status- en resultaatweergave van bron 30905cf1 is live sinds
12 september 2026 om 18:52 CEST, in de gezamenlijke image 4a5fa7c1.

Voer dit in je bestaande privégesprek met Eva uit:

1. Stuur `/eva2 Laat een hulpagent een uitgebreid voorstel bedenken voor een gezellige gezinsdag zonder iets te boeken of te kopen`.
   Verwacht: Eva start één hulpagent en geeft een taaknummer, of meldt eerlijk dat de taak al klaar is.
2. Stuur, zodra de taak is gestart, als **afzonderlijk bericht** `/eva2 Stop mijn onderzoek`.
   Verwacht zolang de taak nog loopt: de stop is opgeslagen; de taak wordt niet vanzelf hervat en het resultaat van die poging wordt niet geleverd.
3. Stuur `/eva2 Hoe staat mijn onderzoek?`.
   Verwacht: de laatste taak met taaknummer en poging staat gestopt, of de stopopdracht is nog in verwerking. Een eerder startantwoord kan door gelijktijdigheid later aankomen; gebruik deze nieuwe status als actuele bevestiging.
4. Stuur `/eva2 Hervat expliciet mijn gestopte hulpagenttaak <taaknummer>. Boek of koop niets.` en vervang `<taaknummer>` door het nummer uit de status.
   Verwacht: precies één nieuwe, gekoppelde poging. Vraag zo nodig de status opnieuw op tot die klaar is.
5. Stuur `/eva2 Toon het resultaat van mijn onderzoek`.
   Verwacht: een leesbaar voorstel met samenvatting en bevindingen, zonder aankopen of boekingen.

Als de helper al klaar is voordat je stopt, is “er loopt geen onderzoek” een correct antwoord. Hervatten van een geslaagde taak hoort geen extra poging te starten. Dat bewijst alleen nog geen onderbreking tijdens actief werk; meld in dat geval wat je zag, zonder dezelfde proef eindeloos te herhalen.

Geef na afloop aan: kon je de helper starten, stoppen, hervatten en het resultaat lezen, en was duidelijk welke poging actief was? Er wordt geen acceptatie afgeleid uit het ontbreken van een reactie.
