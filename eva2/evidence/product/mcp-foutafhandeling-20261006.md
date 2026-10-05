# MCP-foutafhandeling

Volgens de officiële Python SDK-clientdocumentatie levert een mislukte toolaanroep een normaal CallToolResult met is_error=True op, met fouttekst in content. De clientaanroep werpt daarvoor geen uitzondering. Controleer is_error voordat je structured_content vertrouwt. Ook een onbekende tool krijgt volgens deze documentatie deze resultaatvorm.

Een JSON-RPC-fout is daarentegen een error-antwoord in plaats van een result; de beschreven Client werpt dan MCPError op. Verwar dus een mislukte tooluitvoering niet met een foutantwoord op protocolniveau.

De fouttekst in content is bedoeld voor het model: zij vertelt wat misging, zodat het model de aanpak of argumenten kan aanpassen en opnieuw proberen. Alleen de vlag meldt mislukking, niet de oorzaak. Het documentatievoorbeeld geeft bij een ontbrekende boektitel de ToolError-tekst terug. Bij andere uitzonderingen kan de tekst beperkt blijven tot een algemene melding.

Bron: https://py.sdk.modelcontextprotocol.io/client/#is_error-whether-the-tool-failed
Herkomst gecontroleerd via https://github.com/modelcontextprotocol/python-sdk : de officiële repository verwijst naar deze documentatiesite en de clientpagina verwijst terug naar de repository.

Uitvoering: de clientpagina is in Eva's eigen beheerde browser geopend en de volledige artikeltekst is daar afzonderlijk uitgelezen. De bevindingen hierboven komen overeen met die browserlezing. De wederzijdse bronverwijzingen zijn tevens gecontroleerd via de bestaande URL-ophaaltool. De eerdere browserblokkade is opgeheven; browseronderzoek is nu uitgevoerd. Geen persoonlijk Chrome-tabblad gebruikt, geen huisinstellingen gewijzigd en geen routine aangemaakt. Dit verslag betreft documentatieonderzoek, geen uitgevoerde SDK-test.
