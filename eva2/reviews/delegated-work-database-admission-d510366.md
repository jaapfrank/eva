# Onafhankelijke review — gedelegeerd werk in de bestaande taakrij

Exact checkpoint: `d5103665ba76555d4d09a9a0c635151e05311f7a`

## Conclusie

`GO` voor alleen WB01b: PostgreSQL mag `delegated_work` nu als zesde taaksoort
in Eva's bestaande `attention_items`-rij bewaren. Dit maakt geen tweede
taakdatabase en bewijst nog niet dat Eva al een subagent kan starten.

## Zelfstandig gecontroleerd

- De migratie wijzigt uitsluitend de bestaande taaksoortconstraint: de vijf
  oude soorten blijven gelijk en `delegated_work` komt erbij.
- Een bestaande `continuation`-taak blijft bij upgrade en geldige downgrade
  bytegelijk.
- Een onbekende taaksoort blijft geweigerd.
- Downgrade met een bestaande gedelegeerde taak faalt atomair en verwijdert of
  verandert niets. Na bewuste verwijdering van die taak werkt rollback wel.
- De historische 64-contractbaseline en oude portable-exportbytes blijven
  ongewijzigd; het huidige register telt 71 contracten.
- De reviewer herhaalde 18 statische tests en de Git-archivecheck. De aparte
  PostgreSQL 16.13-proef sloot de reeks op 19 groene tests.
- Het exacte Git-archive bindt 43 release-inputs en 59 testbestanden, gebruikte
  geen netwerk en is niet gebouwd of gedeployd.

De twee SQLAlchemy-waarschuwingen over bestaande cyclische foreign keys zijn
voor deze ene handmatige checkconstraint geen blocker. Ze blijven wel een
expliciet aandachtspunt zodra een latere migratie tabellen of zulke foreign
keys wijzigt.

## Niet bewezen

Dit checkpoint bewijst geen runtimewriter, lease/reclaim, worker, modelcall,
subagent, browser, Kanban, build, deploy of B-adoptie. De volgende afzonderlijke
stap moet de duurzame handoff plus portable-exportgrens sluiten; daarna volgen
lease, eenmalige call-start en crashherstel.
