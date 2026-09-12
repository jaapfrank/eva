# Gedeelde schemavalidatie zonder connectorafhankelijkheid

De initiatiefopslag importeerde een private validator uit de concrete MCP-netwerkadapter. Dezelfde bestaande functie staat nu openbaar in `eva.application.schema_validation`; gateway en initiatiefopslag gebruiken één implementatie. Een nieuwe bibliotheek of schema-engine is niet nodig: de bestaande beperkte subset is onderdeel van toegelaten toolargumenten, en vervanging zou de geaccepteerde waarden kunnen veranderen.

De functie is na alleen de naamswijziging AST-gelijk aan de bestaande implementatie. Fouttype, fouttekst, recursie, providerdefaults en de strengere initiatiefschema- en patrooncontrole zijn behouden. Dit ondersteunt niet ineens alle JSON Schema-constructies: onder meer het bestaande integergedrag voor `number` blijft gelijk. Gatewayauthenticatie, registry, credential-I/O en ledger zijn niet gewijzigd.

81 bestaande gateway/profieltests en 6 echte PostgreSQL-proeven slagen, inclusief duurzame herlezing, geweigerde mandaten en stoppen tijdens toelating. PostgreSQL was tijdelijk, zonder netwerk of gepubliceerde poorten; geen productiegegevens of modelcalls zijn gebruikt.

De ongewijzigde architectuurpoort van canonical baseline a0cd0ec9 gaat van 28 naar 27 bevindingen: precies de concrete MCP-import verdwijnt en er komt niets bij. Deze canonical telling is niet de afzonderlijke live-imagetelling van c992.

Status: gebouwd als bron en gericht getest; onafhankelijke review aangevraagd. Geen image gebouwd of livepromotie. De overige persistence-/credential-/registrygrenzen blijven vervolgwerk in het bestaande werkbord. Deze bronwijziging kan na review worden gebundeld met een geschikte volgende release, met exact imagebronbewijs en bestaande terugweg.
