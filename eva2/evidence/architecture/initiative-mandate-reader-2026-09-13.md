# Actuele, hostgebonden mandaatlezing

De opslagadapter krijgt nu een `Callable[[], str]` die bootstrap met de bestaande geharde bestandslezer en het ingestelde mandaatpad samenstelt. De adapter kan geen ander bestandspad meegeven. Iedere controle leest opnieuw; dezelfde parsing, foutomzetting, eigenaarcontrole, digest, enabled-vlag en duurzame mandaattransacties blijven bestaan. Het fallbackpad vanuit EVA_MCP_AUTONOMY_MANDATE_FILE blijft gelijk. Er is geen cache, nieuw framework, gewijzigde autorisatie of ledger toegevoegd.

37 echte PostgreSQL-initiatiefproeven slagen. Vijf nieuwe gevallen veranderen het mandaat nadat de tool is voorbereid maar voordat dispatch mogelijk is: disabled, ontbrekend, groepsleesbaar, symlink en ongeldige JSON. In elk geval zijn er nul providerdispatches, geen uitvoerbestand en geen INITIATIVE_DISPATCH_ADMITTED. Ook een nieuw samengestelde authority weigert hetzelfde gewijzigde bestand. De tests gebruiken de echte bootstrapcompositie en beveiligde reader; een tussentijds veranderd environmentpad leidt de bestaande callback niet naar een ander geldig mandaat.

De database was tijdelijk, zonder netwerk of gepubliceerde poorten, en is verwijderd. Geen productieverbinding, modelcall of live mandaatwijziging. Een nieuwe authority-instantie is getest; dit is geen OS-procesherstartproef. De bestaande veiligheid is behouden en de verantwoordelijkheidsgrens expliciet gemaakt; er is geen claim van een eerder aangetoonde autorisatiekwetsbaarheid.

Op exacte canonical baseline 68a9c705 daalt de architectuurpoort van 25 naar 24: alleen persistence→credentials verdwijnt, zonder nieuwe bevinding. Dit is geen live-imagetelling of volledige architectuur-GO.

Status: bron gebouwd en getest, onafhankelijke review aangevraagd. Geen imagebouw of installatie. Bij bundeling mag uitsluitend deze kleine runtime.py-hunk worden toegepast; CP001-gebruiksregistratie en nog niet live householdbron blijven afzonderlijk. Een gecombineerde release vereist bron-/imagepariteit en de bestaande terugweg.
