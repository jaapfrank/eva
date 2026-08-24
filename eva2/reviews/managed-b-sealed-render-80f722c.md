# Onafhankelijke review — verzegelde managed-B-render

Checkpoint: `80f722c`
Uitgevoerd: 24 augustus 2026
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

## Gecontroleerd

- Het B-envbestand kan zijn eigen configuratiehash niet bevatten; de hash wordt
  uit één eerder gevalideerde snapshot afgeleid.
- De Compose-template is met een verplichte SHA-256 aan de render gebonden.
- De renderer maakt een tijdelijke directory `0700` en renderfile `0600` uit
  uitsluitend die immutable snapshot. Het oorspronkelijke B-envbestand wordt
  daarna niet opnieuw geopend.
- Gedeeltelijke schrijfacties worden volledig afgemaakt; nul voortgang faalt.
- Alleen gepinde Docker-binary/config/context en `compose version` / `config
  --format json` worden aangeroepen. Er is geen create, start, stop, recreate
  of andere B-mutatie.
- De gerenderde acht services, labels, interpolatie en secretbronnen worden
  fail-closed gecontroleerd.

## Resultaat

De review vond eerst een externe env-file TOCTOU en daarna een partial-write
randgeval. Beide zijn in hetzelfde kleine checkpoint opgelost en adversarial
getest. De finale review is groen; de reviewer heeft B, Docker en Git niet
gewijzigd.

Lokale verificatie: 39 gerichte tests, compile, Ruff en cached diff-check groen.
Dit is renderbewijs, geen B-adoptie of live-startbewijs.
