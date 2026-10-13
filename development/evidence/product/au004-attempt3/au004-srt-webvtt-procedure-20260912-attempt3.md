# Lokale procedurekandidaat: eenvoudige SRT naar WebVTT

Proef: AU004-20260912-attempt3. Geen globale installatie of nieuwe bevoegdheid.

## Bron en bereik
Primaire bron: [W3C WebVTT, Candidate Recommendation Draft van 20 mei 2026](https://www.w3.org/TR/2026/CRD-webvtt1-20260520/), met name §4.1. De onderzoeksdienst heeft deze bron daadwerkelijk gelezen. Deze instructie is mijn beperkte toepassing van de gerapporteerde bronregels, geen volledige SRT-parser of WebVTT-validator.

Invoer: unieke numerieke cue-nummers, één timingregel per cue in HH:MM:SS,mmm --> HH:MM:SS,mmm en één of meer gewone, niet-lege tekstregels. Cueblokken zijn door een lege regel gescheiden. Behoud nummers, tekst, accenten, interpunctie en tekstregelgrenzen. Geen vertaling, trimmen of Unicode-normalisatie.

## Bronregels
WebVTT gebruikt UTF-8, een WEBVTT-header en een lege regel vóór de cues. Cue-identifiers zijn optioneel; hier blijven de unieke nummers staan. Cues worden met een lege regel gescheiden. Tijdstempels gebruiken een punt vóór precies drie millisecondecijfers; uren hebben minstens twee cijfers en geen 23-uursgrens, minuten en seconden liggen in 00–59. Eindtijd is strikt groter dan begintijd; begintijden zijn niet-afnemend. Overlap en gelijke begintijden mogen bij ondertitels. Markup en tekenreferenties worden geïnterpreteerd; --> mag niet letterlijk in cue-tekst voorkomen. Zie de primaire bron hierboven.

## Werkwijze
1. Controleer vóór schrijven dat het doelpad niet bestaat; stop bij een bestaand bestand.
2. Splits in cueblokken en valideer nummer, timing en niet-lege tekst. Deze beperkte invoervorm eist precies twee uurcijfers en drie millisecondecijfers.
3. Vergelijk tijden als milliseconden: ((uren × 60 + minuten) × 60 + seconden) × 1000 + milliseconden. Controleer bereiken, eind > begin en niet-afnemende begintijden.
4. Zet WEBVTT en een lege regel vooraan. Behoud cue-nummers. Vervang uitsluitend de komma in elk tijdstempel door een punt; verander geen tekstkomma's. Gebruik één spatie aan weerszijden van -->.
5. Kopieer alle tekstregels ongewijzigd. Scheid cues met een lege regel; eindig met een regeleinde. Schrijf als UTF-8, bij voorkeur met LF en zonder BOM.
6. Lees het bestand terug en vergelijk met invoer en verwachte uitvoer. Meld afzonderlijk of controles tekstueel of met code zijn uitgevoerd.

## Afwijzingsgronden en toepassingsgrenzen
Wijs ontbrekende/dubbele nummers, ongeldige of ontbrekende tijden, eind <= begin, afnemende begintijden, lege tekst en dubbelzinnige interne lege regels af. Geen automatische reparatie.
Deze behoudende procedure wijst tekst met &, < of > af, inclusief markup, tekenreferenties en -->: escaping of interpretatie zou buiten letterlijk tekstbehoud vallen. Dat is een lokale beperking, geen algemeen verbod op deze tekens in WebVTT. Ook positioneringscodes, cue-instellingen, styling en overige SRT-uitbreidingen vallen buiten bereik. Uren met meer dan twee cijfers vallen buiten deze invoervorm, hoewel WebVTT ze toestaat. Overlap alleen signaleren, niet als algemene fout behandelen.

## Controlelijst
- Nieuw doelpad; bron en beperkte scope vastgelegd.
- WEBVTT-header, lege scheidingsregels en afsluitend regeleinde.
- Evenveel cues en tekstregels; nummers en tekst exact behouden.
- Alleen timingkomma's gewijzigd; drie millisecondecijfers en geldige bereiken.
- Eind > begin; begintijden niet-afnemend; eventuele overlap gemeld.
- Geen niet-ondersteunde tekstsyntaxis of instellingen.
- Teruglezing uitgevoerd; geen claim van parser-, browser- of bytevalidatie zonder uitvoering.
