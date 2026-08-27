# Review — AgentRL artifactherstel `f4fe2c2`

Datum: 27 augustus 2026  
Reviewer: onafhankelijke projectstatus-audit

## Beslissing

**GO, maar alleen voor de kleine bouwstap.** De bewaarde controller- en
worker-inputs, hun validators en de netwerkloze rebuild zijn nu `tested` en
`reviewed`.

**NO-GO voor de benchmark zelf.** De scan telt nog 4 kritisch + 39 hoog voor de
oude controller en 9 kritisch + 63 hoog voor de worker. Er mag daarom geen
AgentBench-taak, developmentstack, score of productiegebruik starten.

## Gecontroleerd

- De oude controller-provenance zegt eerlijk dat zij historisch onvolledig is
  en verwijst naar de herhaalbare v2-route.
- De workerprovenance bevat nu de echte lock-opdracht, de 56 gewone wheels en
  het aparte `etcd3`-recept.
- De oude protocolproef is duidelijk een lege, eerdere registratie op andere
  images; zij telt niet als bewijs voor de retained of v2-images.
- Vier controllerbestanden en 57 workerwheels zijn alleen-lezen en bytegelijk
  aan hun manifests.
- Beide fail-closed validators zijn groen, evenals 20 gerichte tests en Ruff.
- De v2-controller is vanaf twee schone paden met gelijke modulecache en
  generators gebouwd; de image bouwt zonder netwerk en beantwoordt `--help`
  als niet-root in een read-only container.

## Volgende echte stap

Niet meer bewijs rond dezelfde kwetsbare bytes verzamelen. Eerst een
onderhoudbare AgentRL/dependency/base-image-upgrade maken, daarna de nieuwe
controller én worker opnieuw bouwen en scannen. Alleen een schone scan opent
de developmentstack.
