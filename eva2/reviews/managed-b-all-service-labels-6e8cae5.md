# Onafhankelijke review — alle managed-B-servicelabels

Checkpoint: `6e8cae5`
Uitgevoerd: 24 augustus 2026
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

De validator eist exact de acht services uit het manifest en voor elke service
hetzelfde managementlabel en dezelfde configuratiesnapshothash. Ontbrekende
services en één afwijkende hash falen. De renderer gebruikt dezelfde centrale
servicelijst, zodat template- en post-startcontrole niet kunnen verschillen.

41 gerichte preflight/renderchecks, compile en Ruff waren groen. Dit is nog
geen live post-startreceipt: de volgende integratiestap moet Docker-metadata van
alle acht containers ophalen en aan deze validator aanbieden. B is niet geraakt.
