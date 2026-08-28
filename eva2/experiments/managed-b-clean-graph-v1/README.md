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
