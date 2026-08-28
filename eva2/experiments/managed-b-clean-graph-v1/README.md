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

Een nieuw gebouwde core kan alleen als expliciete lokale digest worden
beproefd. Een tag of `latest` wordt geweigerd:

```bash
sudo -n python3 experiments/managed-b-clean-graph-v1/probe.py \
  --clone-current-route \
  --core-image eva2/candidate@sha256:<64 hextekens>
```

Na een geslaagde wegwerpproef kan exact één vast tweede slot worden
klaargezet. Dit gebruikt poort `44709`, project `eva2-managed-b-slot2` en een
private configuratiemap onder `/var/lib/eva2`. Bestaande slotmiddelen worden
geweigerd. De bron blijft alleen-lezen; beide dumps worden na herstel gewist.
Bij een fout worden uitsluitend de nieuw gemaakte slotcontainers en -volumes
opgeruimd. Deze opdracht schakelt de `/eva2`-route nog niet om. De historische
`live-test`-gespreksscope blijft behouden; het eigenaar-dashboard gebruikt een
aparte, strikt UUID-gebonden beheerscope:

```bash
sudo -n python3 experiments/managed-b-clean-graph-v1/probe.py \
  --clone-current-route \
  --retain-slot \
  --core-image eva2/candidate@sha256:<64 hextekens>
```
