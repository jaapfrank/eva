# AU-008 — technische beoordeling van private initiatiefcyclus

Status: technische source-GO; productacceptatie open. Vastgelegd 10 september 2026.

De onafhankelijke reviewer `au002_release_review` (Chandrasekhar) beoordeelde
zeven bron- en testpaden van commit `519a03a88c02e64d3ab8bb005d9a2168c0f9ac95`.
Hun diff tegen `af9a049a5dcd9b739b74e10252f6688d7b90bb14` heeft SHA-256
`195c10be6103644a7e02c5b784059507f6d1327338ce4f4c94ba59612b720a4f`.
De gegenereerde inventorywijziging viel buiten die review.

De private bron heeft een duurzame eigenaargebonden uurpoort, haalt geen gemiste
vensters in en respecteert bestaand werk. Dezelfde eigenaarautoriteit geldt vóór
geheugenraadpleging, vóór modelstart, bij adoptie en bij uitvoering. De bestaande
dagbudgetreservering draagt de review-ID. Onzekere aanroepen worden niet herhaald.
De host maakt van een timerreview geen proactief bericht of fictieve huiswaarneming.

De bouwer voerde zes gerichte claimgevallen en één admission-herstelgeval uit;
ze slaagden. De reviewer las dit bewijs en controleerde de bron, maar voerde zelf
geen tests of runtimehandelingen uit. De eerste mislukte budget-denialproef blijft
historisch bewaard; de daaropvolgende reparatie is in het geslaagde eindbewijs opgenomen.

De reviewer vergeleek alle negentien image-importhashes met de bevroren bron.
De bouwer controleerde dezelfde negentien geladen modules na de A/B-wissel.
Actuele uitrol- en praktijkstatus: [bewijs](../evidence/product/AU-008-private-review-live-2026-09-10.json).

Dit sluit de technische aansluiting af. Een werkelijk gekozen en afgeronde
bruikbare bijdrage uit de normale cyclus is nog vereist voor AU-008. De huidige
readback toont dat het bestaande eigenaarachtergrondbudget vandaag 4/4 is verbruikt.
Jaaps acceptatie wordt niet afgeleid uit deze code- of runtimecontrole.
