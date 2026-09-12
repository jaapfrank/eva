# Gezamenlijke bron op de huidige image

De gedeelde schemasubset-validator, hostgebonden actuele mandaatlezer en terminale registratie van directe modelpogingen zijn samengevoegd op alle 259 bytegelijk gecontroleerde bronbestanden van live c992. Alleen de afzonderlijk goedgekeurde hunks 7d4d1e8, 5baccfb4, 63363793 en 084cdc82 zijn toegepast. De private uitlijncommit 8f269 is niet toegepast.

De kandidaat heeft 260 bronbestanden, met precies zeven gewijzigde/toegevoegde bestanden. Zes daarvan zijn volledig bytegelijk aan hun goedgekeurde eindbron. runtime.py bevat uitsluitend de freshreader-constructorhunk boven de live bron; de niet live household-router en gedeelde voice-stream ontbreken nog steeds. Deze releasebaseline mag niet integraal naar canonical worden gemerged.

Alle 209 gerichte tests op deze bron slagen: 140 model/gateway/stoptests, 37 initiatief-PG, 9 directe modelpoging-PG, 16 voice-PG en 7 research-PG. PostgreSQL was tijdelijk en netwerkloos; geen modelcalls of productiedatabaseverbindingen. Dit bevat de bestaande stemreplay en onderzoekshervatting naast de nieuwe direct-failure usage en actuele mandaatweigering. Het is geen volledige releasepoort of echte modelkwaliteitsproef.

Status: gezamenlijke bron gereed voor onafhankelijke review. Nog geen image gebouwd of livepromotie. Na bron-GO volgt een image met exacte bron-, configuratie- en laagpariteit op immutable c992. Live blijft c992 tot afzonderlijke image- en cutovercontrole.
