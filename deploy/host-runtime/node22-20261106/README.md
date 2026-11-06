# Node 22.23.4 — bestaande Eva-hostdiensten

Deze onderhoudsrelease gebruikt de beveiligingspatch van de al gebruikte Node22-tak.
Vier werkelijk actieve WSL-hostdiensten gebruiken een eigen, door root beheerde
Linux-executable; de bestaande browserworker gebruikt de bijbehorende Windows-
executable via WSL-interop. Applicatiecode en npm-afhankelijkheden veranderen niet.
De algemene Windows- en Ubuntu-installaties van Node blijven behouden.

## Reproduceerbare herkomst

`vendor-manifest.json` bindt de exacte publieke downloads en uitvoerbare bestanden.
De meegeleverde NodeSource-InRelease tekent de volledige Packages-index; de
reeds vertrouwde NodeSource-sleutel en alle hashes zijn bewaard. Controleer een
gedownload pakket zonder installatie met:

```sh
python3 deploy/host-runtime/node22-20261106/verify_vendor.py /path/to/nodejs_22.23.4-1nodesource1_amd64.deb --windows-executable /path/to/node.exe
```

Dit controleert een gedateerde momentopname, geen actuele updatebeschikbaarheid.
De Windows-executable is bovendien onafhankelijk via Authenticode gecontroleerd:
geldige OpenJS Foundation-handtekening, gebonden aan dezelfde SHA256.
De licenties staan in het NodeSource-pakket onder `usr/share/doc/nodejs/copyright`
en worden bij de beheerde uitvoerbestanden behouden. Er zijn geen binaries in Git.

## Installatie en herstelgrens

De installatie bindt de twee executables aan `/opt/eva2/managed-runtimes/node-v22.23.4/`.
Alleen vier bestaande systemd-ExecStart-instellingen en het veld `workerCommand`
van de bestaande browserconfiguratie veranderen. Alle vier diensten behouden
onbeperkte nette stoptijd en krijgen geen geforceerde SIGKILL. Lopende gesprekken,
werkclaims, Telegramaflevering en browserovername worden vooraf gecontroleerd.
Bestaande configuratie, bronbestanden, autorisatie en berichtenregistraties blijven
gebonden aan hun gecontroleerde hashes. Een proef bewijst lezen en schrijven op
Node22.23.4 én teruglezen op de oude versie zonder een onzekere aflevering te herhalen.

De eerste installatieregistratie blijft exact bewaard: Vite sloot op SIGTERM af met
143, waardoor een te strikte controle na de stop werd geweigerd. Een afzonderlijk
beoordeelde hervatting bindt die code aan precies de oorspronkelijke gestopte
processen. Zij herhaalt geen stop of installatie. Het actuele installatiebewijs
en de onafhankelijke teruglezing staan in
`development/evidence/reassessment/maintenance-updates-20261106/host-node22-installed.json`.

Dit is een begrensde onderhoudsoplevering. Volledige autonome updates, foutreparatie
en later hergebruik van E4.16 zijn hiermee niet afgerond.
