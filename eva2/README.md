# Eva 2

Dit is de nieuwe, connectorneutrale Eva-runtime. Deze map deelt geen uitvoerbare code,
schema-eigenaarschap, secrets of processtate met Eva 1.

De huidige implementatiefase is uitsluitend tranche 4A:

- strikte, versiegebonden contracten en gegenereerde JSON Schema's;
- vaste Python-packagegrenzen;
- numerieke budgetten en een exact platformdesign;
- een hash-locked dependency- en supply-chaindesign;
- statische bewijschecks zonder database, connectorwrites of serviceproces.

De map bevat in deze tranche nog geen werkende agent, installer, connector, migratie of
productieservice. DBOS blijft een voorwaardelijke keuze totdat tranche 4C de atomische
datasource-invariant met echte crashproeven bewijst.

Veiligheid blijft een dunne grens rond echte risico's, geen tweede bestuurssysteem. Lezen,
redeneren, plannen en uitleggen krijgen geen onnodige toestemmingslagen. Alleen externe of
moeilijk omkeerbare handelingen, zoals schrijven naar accounts, publiceren, betalen, verwijderen
en credentialgebruik, passeren de bijbehorende begrensde effectroute.

## Lokale 4A-controle

De drie controles draaien vanuit de repositoryroot met een externe Pythonomgeving die exact
overeenkomt met `eva2/release/gate-toolchain.lock.json`:

```bash
<sealed-gate-python> eva2/tools/run_gate.py --gate CONTRACT-01
<sealed-gate-python> eva2/tools/run_gate.py --gate PLATFORM-DESIGN-01
<sealed-gate-python> eva2/tools/run_gate.py --gate SUPPLY-DESIGN-01
<sealed-gate-python> eva2/tools/finalize_4a_evidence.py
```

De runner installeert niets en gebruikt geen Eva 1-data, database, service of externe connector.
Een ontbrekende of afwijkende toolchain levert `not_proven` op in plaats van een groen resultaat.
