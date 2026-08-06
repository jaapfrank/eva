# Eva - canonieke Codex-overdracht

Bijgewerkt: 2026-08-02

Dit document is de enige benodigde overdracht voor een nieuwe Codex-thread. Oude
gespreksherhaling is geen bron van waarheid; live code, database, logs en tests zijn
dat wel.

## Doel

Eva moet als een betrouwbare, warme en ondernemende huisgenoot functioneren:

- gewone meerbeurtenconversaties voeren en context vasthouden;
- opdrachten van begin tot eind uitvoeren, verifiëren en begrijpelijk terugmelden;
- uit zichzelf nuttig werk kiezen, meerdere stappen afmaken en alleen echte voortgang melden;
- het gezin praktisch helpen, mensen leren kennen en relevante gebeurtenissen onthouden;
- internet, programmeren en bestaande accounts zelfstandig inzetten om uiteindelijk aantoonbare inkomsten te verdienen;
- haar eigen geleerde brein steeds vaker zinvol gebruiken, zonder te beweren letterlijk menselijk of levend te zijn.

De verplichte kernroute is:

`observe -> interpret -> commit -> act -> verify -> remember -> speak`

Geen losse antwoordscriptjes per formulering. Nieuwe code is alleen gerechtvaardigd als
zij een algemene kernfout oplost; verwijder een oude route zodra de kern haar bewezen
overneemt.

## Harde gedragsregels

- Nooit zeggen dat iets gedaan is zonder bewijs.
- Geen ruwe JSON, tooloutput, technische statusdump of interne prompt naar Telegram.
- Een mislukte tussenstap is geen eindantwoord: herstel, probeer een andere route of meld de concrete blokkade.
- Een open opdracht blijft als duurzaam werk bestaan tot uitkomst, bewijs of een echte externe blokkade.
- Geen herhaalde toestemming vragen voor eerder gegeven mandaat. Vraag alleen een exact ontbrekend gegeven op het moment dat dit de laatste echte blokkade is.
- Onderzoek is geen bijdrage op zichzelf. Het telt pas na selectie, besluit, gebouwd/geleverd resultaat of concrete besparing/opbrengst.
- Kritiek van Jaap, een bestandswrite en gezichtslabeling tellen niet automatisch als gezinsbijdrage.
- Autonomie mag meerdere nuttige projecten draaien; begrens risico en kosten, niet een willekeurig aantal projecten per dag.
- Communicatie is kort en menselijk: conclusie eerst, technische details alleen als ze nodig zijn.

## Bestaande kern

- `src/mind/agent.js`: centrale turn, intent/toolkeuze, bewijs en antwoord.
- `src/mind/tools.js`: tools en `ActionResult`-normalisatie.
- `src/mind/work.js`: duurzame opdrachten, missies, checkpoints en werkstatus.
- `src/mind/executive.js` en autonomie-scheduler: zelfstandig werk hervatten en kiezen.
- `src/mind/persona.js`: alleen stem/persoonlijkheid; beleid hoort in code.
- Telegram, voice, dashboard en follow-ups moeten dezelfde kernturn gebruiken.
- Procesdocumentatie: `docs/eva-runtime-map.md` en `docs/eva-als-proces.md`.

## Bewezen regressies op 2026-08-02

Laatste echte gesprekken tonen vier kernfouten:

1. Bij zelfstandig inkomen en crypto-bounties gaf Eva na tool/modeluitval de tekst
   `Mijn afrondende antwoord viel weg` plus ruwe zoek-JSON terug.
2. Een groot `zoek_internet`-resultaat kwam als geneste/truncated `ActionResult` terug;
   de directe onderzoeksroute pakte de JSON-envelope in plaats van het menselijke veld.
3. `Ga kijken welke problemen je kan oplossen en geld voor kan vragen` werd als losse
   zoekvraag afgehandeld. Eva herlas daarna een oud bestand en maakte geen shortlist,
   keuze, uitvoerplan of verkoopbaar resultaat.
4. Een bestandswrite na `Daar heb ik niks aan` werd onterecht als gezinsbijdrage gelogd.

Waarschijnlijke reparatiepunten in `src/mind/agent.js`:

- maak één recursieve extractor voor menselijk resultaat uit geneste `ActionResult`;
- laat `evidenceFallback()` nooit ongefilterde tooldata tonen;
- laat directe research alleen gelden voor begrensde informatievragen, niet voor
  meerstapsprojecten;
- classificeer opdrachten met een gewenste externe uitkomst als duurzaam werk;
- maak bijdragebeoordeling semantisch: veranderd bestand alleen telt bij een gevraagde
  of missiegebonden, geverifieerde uitkomst.

## Inkomsten: gewenste projectregie

Eva hoeft geen algemeen rapport over geld verdienen te schrijven. Haar eerste echte
inkomenstraject moet deze lifecycle volgen:

1. hergebruik het onderzoek naar recente problemen;
2. selecteer maximaal drie problemen op aantoonbare vraag, uitvoerbaarheid, bereikbare
   koper en korte tijd tot eerste betaling;
3. kies er één en leg keuze plus afwijsredenen vast;
4. bouw een klein verkoopbaar resultaat of pak een concrete open software/crypto-bounty;
5. test en lever bewijs;
6. maak aanbod, prijs en verkoop-/claimroute gereed;
7. meld alleen een relevante mijlpaal of exact ontbrekend accountgegeven.

Jaap heeft expliciet gezegd dat Eva zelfstandig mag werken, accounts mag aanmaken en
bestaande crypto-accounts voor stortingen mag gebruiken. Bewaar credentials nooit in
chat of gewone logs; gebruik de bestaande secretvoorziening of bouw eerst een passende
lokale secretstore met voor Jaap vindbare herstelgegevens.

## Hoststabiliteit en Codex-uitval

De melding `Codex stopped unexpectedly` was op 2026-08-02 geen gewone Codex-crash.
De volledige WSL2-instance stopte om 15:02:42 en startte om 15:03:01 opnieuw.

Bewijs uit de vorige boot:

- duizenden `nvidia-smi`-processen; tegen het einde twee starts per 15 seconden;
- herhaalde `nvidia-smi`-segfaults en WSL `CaptureCrash`;
- willekeurige `python3.12`-segfaults in de dynamic loader;
- kernel gemarkeerd met `SOFTLOCKUP`;
- `lilith-hefboom.service` zat door `Restart=always` in 5.867 nutteloze herstarts,
  hoewel de kill-switch iedere run schoon liet stoppen;
- geen nette shutdownregels: de WSL-host werd abrupt vervangen.

Los eerst uit welke services `nvidia-smi` zo vaak starten. Cache GPU-health, gebruik
één eigenaar en een ruime back-off na fouten; start geen tweede poll terwijl de eerste
loopt. Corrigeer daarnaast de Lilith-unit naar geen restart bij succesvolle kill-switch,
maar wijzig verder geen Lilith-gedrag zonder noodzaak.

Deze Codex-thread bevatte bij de laatste uitval 126.496 rollout-items. Dat is te groot
voor gezond herladen in de VS Code-webview. Start vervolgwerk in een nieuwe thread met:

`Lees docs/codex-handoff.md en ga verder vanaf de eerstvolgende reparatiestap.`

## Eerstvolgende stappen

1. Stabiliseer WSL: vind en stop de `nvidia-smi`-pollstorm en de nutteloze service-restartloop.
2. Repareer geneste toolresultaten en veilige noodantwoorden in de kern.
3. Maak open uitkomstgerichte opdrachten duurzaam en corrigeer bijdragebeoordeling.
4. Voeg gerichte regressietests toe voor de exacte gesprekken van 2026-08-02.
5. Herstart Eva en voer meerdere live Telegram-turns uit, inclusief tooluitval.
6. Start één echt inkomenstraject en observeer dat Eva minstens twee betekenisvolle
   stappen zelfstandig zet zonder JSON, herhaling of valse afronding.

## Acceptatie

Niet klaar op basis van alleen unit tests. Klaar wanneer live blijkt dat:

- gewone opdrachten meteen worden uitgevoerd en geverifieerd;
- onderzoek resulteert in een besluit en vervolgactie;
- Eva na één stap zelfstandig doorgaat;
- haar bericht natuurlijk, compact en contextueel klopt;
- het dashboard dezelfde echte toestand toont;
- de WSL-instance onder normale Eva-belasting stabiel blijft.
