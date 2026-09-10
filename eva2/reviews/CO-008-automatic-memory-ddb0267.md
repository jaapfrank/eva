# CO-008 — onafhankelijke technische review

Besluit: **GO binnen de vastgelegde CO-008-scope.**
Reviewer: `/root/au002_release_review`, onafhankelijk van bouwer `/root`.
Review ontvangen op 10 september 2026 via de bestaande reviewagent.

## Gebonden implementatie en bewijs

- Basis: `0126ac0a1e0c3962ae4f0863f2d695e6206bdabc`.
- Eindcommit: `ddb0267852c56c95ccde4270d821e3192ba56b70`.
- Exacte reeks: `003927f → 00002b9 → 0e24742 → ddb0267`.
- Test-/livebewijscommit: `ef600df0810c0ed5c59056bf22e683513eabebb0`.
- Productbewijs SHA-256: `7518bc6cd321c0bf81a5a9ef587ce3a21470ebdd5bc7b818e7b0d085165d9fad`.
- GE-01 acceptatie SHA-256: `9e83709426d618a87d39a30400b2e148e55810e9ea8eafb4cda89f0a2b541ecc`.

De reviewer controleerde de bron, alle 33 artifactHashes en 13 antwoordteksthashes.
De definitieve testlog bevat 62 geslaagde adaptertests in 31,34 seconden; oudere
7/9 resultaten zijn alleen samenvattend vastgelegd. Het definitieve image
`sha256:3a508a55df506646cb02b26e1944d3b34d7588a2b5ca57a34902fd1c649c9e23`
en beide runtime-bronhashes zijn aan de eindcommit gebonden.

## Beoordeeld gedrag

Duurzame retries behouden het juiste taakobject en maximaal drie pogingen;
annuleringen worden niet opnieuw gestart. Eerste antwoorden halen geheugen op;
cachegebruik is gebonden aan vraag, tijd, persoon en privacy. De beste
laag scorende kandidaat passeert dezelfde bron-, persoons- en privacycontroles.
Live receipts ondersteunen voorkeurretentie, correctie, impliciete toepassing
na herstart, tijdelijke context, persoonsisolatie en nul semantische feiten uit ruis.
Jaap heeft GE-01 geaccepteerd. Deze proef blijft gesloten.

## Grenzen

Twee HTTP 503-fouten zijn niet als opgelost bewezen; de persoonsproef herstelde
via dezelfde turn. 34 historische needs_help-records blijven bestaan. De reviewer
kon de schermafbeeldinghash niet binnen de private artifactset herberekenen;
de expliciete eigenaarsacceptatie staat afzonderlijk vast en is geen volledige
gezinsacceptatie. De latere voicefix
`4c24a7f6a5c767ad0c116b23bb4251d9908908d4` is een herkenbare opvolger voor
begrensde bronverwijzingen en heropent GE-01 niet.

De reviewer heeft uitsluitend bron, bewijs en Git-objecten gelezen; geen tests,
bestandswijzigingen of runtimeacties uitgevoerd.

## Werkbordadministratie

Dezelfde onafhankelijke reviewer gaf GO voor de regelwijziging die exacte
lineaire implementatiereeksen en latere, uitsluitend bewijs bevattende commits
toestaat. Merges, onbekende basis, tussentijdse wijzigingen buiten scope en
verkeerde bewijsbestanden blijven geweigerd. Vijf gerichte regressies slaagden
in 0,53 seconden. Geen historie herschreven.

Beoordeelde validatorhash: `3905db2303f3f76d9ac0e469ea688b21cc230b9002f3dcef3dfa235610665888`.
Beoordeelde regressiehash: `dc20b689a3b795377fafa606f75531b4ded2d8ec5059e45c04fb1b61e9dbcdfc`.
