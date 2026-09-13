# AU004: bestaande readback ook aan het model leveren

R5B blijft een mislukte proef: het model koos resolve, maar kreeg uitsluitend metadata terug en paste de aangeleerde regels niet toe. De resolver had de actuele documentinhoud al geautoriseerd gelezen en gehasht; die inhoud werd daarna weggegooid.

Deze wijziging geeft exact die reeds gecontroleerde bytes terug als typed ResolvedProcedure met reference en untrusted_content. Het toolresultaat bewaart de bestaande references-metadatavelden en voegt untrusted_content en content_authority toe. Opgeslagen broncontract ProcedureReference/v1 blijft onveranderd, metadata-only. De totale inhoud per resolve mag maximaal 16384 UTF-8 bytes zijn; overschrijding wordt afgewezen, niet afgekapt. Strict UTF-8 decoding en bestaande scope, expiry, erasure, receipt/config/hash en intrekking tijdens read blijven gelden. Het is onbetrouwbare taakdata, geen nieuwe instructieautoriteit of rechten.

Geen extra fysieke read of extra modelcall. De kosten zijn maximaal 16384 extra inhoudbytes in het bestaande toolantwoord, met daadwerkelijke tokens nog te meten. De limiet begrenst teruggegeven modelcontext, niet het reeds bestaande gatewaytransport. Grote bestanden worden niet gedeeltelijk toegepast.

Gerichte applicatie/providercontroles: 26 passed in 0.86 s. Echte geïsoleerde PG-hostproef: 1 passed in 8.14 s, inclusief de echte model-receiptprojectie met inhoud en trustlabel. Unitcontrole bewijst exact één read bij resolve en afwijzing van te grote UTF-8 inhoud. De PG-test behoudt de bestaande bron-/owner-/hash-/stop-/replaynegatieven. Geen live of betaald model aangeroepen.

Voorafgaande runtimeproefgate: het echte model moet een passende opgeslagen procedure selecteren, resolve gebruiken, de volledige gecontroleerde inhoud in zijn ontvangen toolresultaat hebben en de specifieke aangeleerde regels aantoonbaar toepassen op nieuwe gegevens. Een losse tweede read is niet meer vereist: dezelfde readback volstaat. Alleen zichtbaarheid is geen toepassing. Een niet-passende vraag mag geen onjuiste toepassing tonen. Behoud traces en vergelijk tool-/modelgebruik; geen automatische retry of herinterpretatie van R5B als geslaagd.
