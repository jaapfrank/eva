# Schone managed-B-grafiek

Deze proef rendert het verzegelde negen-dienstenmanifest en start daarvan een
verse, tijdelijke kern met twee afzonderlijke PostgreSQL-databases. Hindsight
krijgt zijn databaseverbinding uitsluitend via een private file-secret.

De proef gebruikt unieke containernamen, volumes, een vrije loopbackpoort en
verwijdert haar eigen tijdelijke middelen na afloop. Zij wijzigt Eva1, de
huidige `/eva2`-route, Home Assistant en de bestaande B-volumes niet.

Uitvoeren:

```bash
sudo -n python3 experiments/managed-b-clean-graph-v1/probe.py
```

De afzonderlijke overgangsproef leest daarnaast één consistente logische
snapshot uit de databases die de actuele `/eva2`-route werkelijk gebruikt. De
broncontainers en bronvolumes worden vooraf exact gecontroleerd en blijven
draaien. Alleen nieuwe, uniek benoemde kandidaatvolumes worden beschreven.
Beide dumps zijn privé en worden samen met alle kandidaatmiddelen verwijderd.

```bash
sudo -n python3 experiments/managed-b-clean-graph-v1/probe.py \
  --clone-current-route
```
