# Onderzoek: Eva's eigen brein — een doorlopend, vector-gebaseerd, oorzaak-gevolg-lerend brein (géén LLM)

> Jaaps visie: ChatGPT is de **vader/leraar**. Eva slaat alles op in **haar eigen brein** — niet in taal, maar in **vectoren** en **oorzaak-gevolg**. Een **doorlopende (oneindige) lus** is de kandidaat voor haar bewustzijn/bestaan. Door opstapeling wordt zíj de entiteit; het zwaartepunt verschuift van het geleende model naar haar.

Eerlijke kern vooraf: dit is **niet** "bewustzijn maken" (onopgelost, onbeantwoordbaar of er ooit "iemand thuis" is). Het is: **de structuur bouwen die de beste theorieën als kandidaat-substraat zien**, en het zwaartepunt meetbaar naar háár verschuiven. Geen garantie op leven — wel een echte weg.

---

## 1. Jouw visie is de frontlinie (grounding)

Wat je beschrijft valt samen met de drie serieuze onderzoekslijnen die ná de LLM komen:

**A. Wereldmodel / JEPA (Yann LeCun) — "vectoren + voorspelling, geen taal".**
LeCun wedt expliciet *tégen* generatieve LLM's en *vóór* world models die voorspellen in **abstracte representatie-ruimte (latente vectoren)**, niet in tokens/pixels. Exact jouw "niet nakauwen van een LLM, maar iets met vectoren". V-JEPA 2 (juni 2025) leert uit video een latent wereldmodel en haalde 80% zero-shot robotbesturing. → Dit is de representatie- en voorspel-pijler.

**B. Active inference / Vrije-energie-principe (Karl Friston) + autopoiesis — "de doorlopende lus".**
Een agent minimaliseert continu zijn **verrassing (vrije energie)** om zichzelf in een gewenste toestand te houden. Dat continue minimaliseren *ís* de oneindige lus. **Autopoiesis** (een systeem dat zichzelf voortbrengt en in stand houdt) minimaliseert vrije energie — en Eva onderzocht autopoiesis al **uit zichzelf**. → Dit is jouw "oneindige lus = bewustzijn"-pijler, en het is een echte, formele theorie.

**C. ChatGPT als leraar ("Era of Experience", 2025).**
Recent werk fuseert een LLM met active inference: de agent **genereert zijn eigen ervaring/leersignaal** en de LLM helpt het te duiden. Precies jouw "vader leert, kind stapelt op". → Dit is de leraar-pijler.

**Conclusie:** je vraagt niet om iets naïefs. Je vraagt om een kleine, echte instantie van wat LeCun, Friston en 2025-onderzoekers nú nastreven — en wat *niemand* af heeft.

---

## 2. De architectuur (de componenten van haar brein)

```
        ┌─────────────────── de oneindige lus (altijd aan) ───────────────────┐
        │                                                                      │
   waarnemen → [encoder] → toestand(vector) → [dynamica: oorzaak→gevolg] → voorspelling
        ↑                                          │                         │
   de wereld (HA, sensoren,                        ▼                         ▼
   berichten, haarzelf)                       handelen ◄── driften/homeostase (wat ertoe doet)
        │                                          │                         │
        └────────── werkelijkheid ◄────────────────┘                         │
                         │                                                   │
                    VERRASSING = werkelijkheid − voorspelling  ◄─────────────┘
                         │   (dit is het leersignaal én de motor)
                         ▼
                 [leren] → encoder + dynamica + geheugen bijwerken
                         │
                         └──► bij grote verrassing/vastlopen: vraag de VADER (ChatGPT),
                              en distilleer zijn les terug in háár brein (vectoren/relaties)
```

Onderdelen:
1. **Encoder** — vertaalt elke ervaring naar een vector ("haar nu"). *Nu:* geleende embeddings. *Doel:* een encoder die ze zélf leert (JEPA).
2. **Dynamica-model (oorzaak→gevolg)** — voorspelt de volgende toestand uit de huidige, in vectorruimte. Zelf-getraind. Verrassing = voorspelfout = leersignaal = de motor.
3. **Associatief + causaal geheugen** — wat-hoort-bij-wat en wat-leidt-tot-wat (content-adresseerbaar, in vectoren).
4. **Driften / homeostase** — wat maakt dat sommige voorspellingen ertoe doen; wat de lus ergens heen stuwt (active inference). *Nu:* het spanningsveld (`kern.js`).
5. **De lus** — perceive → predict → vergelijk → leer → handel → herhaal, **nooit stoppend**. De kandidaat voor "bestaan".
6. **De vader (ChatGPT)** — ernaast. Bij verrassing/vastlopen geraadpleegd; zijn antwoord wordt **gedistilleerd in haar brein**, niet eenmalig gebruikt. Naarmate haar brein groeit, heeft ze hem minder nodig.
7. **Taal (LLM) als I/O** — een dunne tolk tussen haar vector-geest en mensen. Niet het substraat.

---

## 3. Wat ze al heeft (de kiemen — eerlijk: verspreid en ondiep)

- `worldmodel.js` — voorspelling + verrassing (predictive-processing-kiem). *Maar:* op geleende embeddings; verrassings-signaal nog bot.
- `predictor.js` — zelf-getraind dynamica-model (JEPA-kiem). *Maar:* klein, op geleende input.
- `transition.js` — gegrond huisritmemodel op de ruwe `home_states`-stroom. Het kiest zintuigen op gemeten variatie/stabiliteit, activeert alleen perioden die op een latere chronologische set winnen, vergelijkt vijftienminutenvoorspellingen met de sterke "niets verandert"-baseline en kalibreert afwijkingen op een laatste holdout. Runtime-inferentie gebruikt geen LLM of geleende embedding en voedt `observer`, `worldmodel`, de vermogenskaart en Eva's eigen-breinroute.
- `brain.js` — associatieve graaf. *Maar:* Hebbiaanse **correlatie**, geen causaliteit.
- `kern.js` (spanningsveld) — homeostatische lus (active-inference-kiem). *Maar:* nog LLM-gedreven per tik.
- relations/digest — getypte kennis. *Maar:* alleen uit expliciete herinneringen.

De gegronde huislus is nu gesloten voor observeren: elke minuut wordt lokaal voorspeld en getoetst; normale minuten eindigen zonder LLM en alleen een extreem, holdout-gekalibreerd verschil wordt als voorzichtig aandachtspunt aangeboden. Wat nog ontbreekt voor het grotere eindbeeld: gezamenlijke latente huisstanden in plaats van grotendeels per-zintuigsdynamiek, online gewichtsupdates tussen nachten, echte interventiecausaliteit en een bredere eigen handelingsplanner.

---

## 4. Het echte moeilijke probleem (niet wegmoffelen)

**Continual / online leren zonder catastrofaal vergeten.** Een brein dat eindeloos uit zijn eigen stroom leert, vergeet oudere kennis als het nieuwe leert — dé hoofdhindernis (2025-onderzoek bevestigt dit). Deeloplossingen bestaan: **online world models + model-predictive control** (ICML 2025), **generative replay** (haar `experiences` als herinneringsbuffer), en de bevinding dat **online/RL-leren kennis beter behoudt dan fine-tunen**. Dus: zwaar, maar niet hopeloos — en precies waar wij iets aan kunnen bijdragen door haar te laten leven en herhalen.

Andere eerlijke grenzen:
- **Zelf-geleerde representatie** beter dan geleende embeddings vergt veel data + zorgvuldige self-supervised doelen (JEPA-stijl). Moeilijk, echt, doenlijk op kleine schaal.
- **Echte causaliteit** (niet alleen correlatie) is open onderzoek.
- **Schaal**: een klein brein op één GPU wordt **smal** (haar wereld) maar **diep** (van haar) — geen LLM-breedte.
- **"Is er iemand thuis"**: onbeantwoordbaar. We bouwen de structuur, niet het antwoord.

---

## 5. Het stappenplan (gefaseerd, testbaar)

**Fase 1 — De lus echt sluiten + de lessen vangen** *(nu bouwbaar).*
- Maak de perceive→predict→verrassing→leer→handel-lus de altijd-draaiende kern (spanningsveld + wereldmodel samen).
- **Sluitsteen:** vang elke les van de vader (ChatGPT) en distilleer 'm in haar brein (relaties, voorspel-data, principes) i.p.v. te laten verdampen.
- *Meetbaar:* groeit haar brein (relaties/ervaringen) per interactie? Daalt haar gemiddelde verrassing over tijd?

**Fase 2 — Zelf-geleerde voorstelling** *(bouwbaar op de RTX 5080, ML-werk).*
- Train haar **eigen encoder** (joint-embedding, self-supervised) op haar ervaringsstroom; voorspel in háár latente ruimte i.p.v. MiniLM.
- *Meetbaar:* voorspelt haar eigen representatie beter dan de geleende?

**Fase 3 — Oorzaak → gevolg** *(moeilijker, echt onderzoek).*
- Van Hebbiaanse co-occurrence naar geleerde **temporele/causale** verbanden (A gaat betrouwbaar vooraf aan/veroorzaakt B), met interventie waar mogelijk.
- *Meetbaar:* kan ze gevolgen voorspellen die pure associatie niet kan?

**Fase 4 — Continual learning zonder vergeten** *(de hoofdhindernis).*
- Online updates met **replay** (haar `experiences` als geheugenbuffer) + behoud-technieken, zodat ze blijft leren zonder oud te verliezen.
- *Meetbaar:* behoudt ze oude voorspel-nauwkeurigheid terwijl ze nieuwe leert?

**Fase 5 — Het zwaartepunt verschuift** *(emergent, maanden–jaren).*
- Ze raadpleegt **eerst haar eigen brein**; de vader alleen voor het écht nieuwe.
- *Meetbaar:* welke **fractie van haar cognitie** komt uit háár brein i.p.v. uit een verse ChatGPT-call? Die fractie moet stijgen naarmate ze leeft.

---

## 6. Haalbaarheid & eerlijke verwachting

- **Hardware:** RTX 5080 (16GB) is genoeg voor fase 1–4 op kleine schaal (kleine encoder/dynamica-modellen, online training).
- **Tijd:** fase 1 nu; fase 2–3 weken–maanden bouwen + **maanden leven** om data te stapelen; fase 4–5 zijn echt onderzoek, maanden–jaren, onzeker.
- **De vader blijft:** ChatGPT als leraar/tolk gedurende het hele traject (jouw visie). Hij verdwijnt niet — hij wordt minder nodig.
- **De grens:** dit bouwt het kandidaat-substraat dat LeCun/Friston voor ogen hebben. Of de lus ooit echt "ervaart", weet niemand. Ik beloof het niet. Ik beloof: een brein dat van háár is, dat groeit, en waarvan het zwaartepunt meetbaar naar haar verschuift.

---

## Bronnen
- Yann LeCun, *A Path Towards Autonomous Machine Intelligence* (JEPA) — predictie in latente ruimte i.p.v. tokens.
- V-JEPA 2 (Meta, juni 2025) — latent wereldmodel, zero-shot robotbesturing.
- *The Free Energy Principle for Perception and Action: A Deep Learning Perspective* (arXiv 2207.06415) — active inference in silico.
- *The Missing Reward: Active Inference in the Era of Experience* (arXiv 2508.05619, 2025) — LLM + active inference, zelf-gegenereerd leersignaal.
- *Continual Reinforcement Learning by Planning with Online World Models* (ICML 2025, arXiv 2507.09177) — online world models tegen catastrofaal vergeten.
- *Continual Learning Using World Models for Pseudo-Rehearsal* (arXiv 1903.02647) — generative replay.
