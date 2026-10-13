# Python: een bestand sluiten bij een fout

Onderzocht op 12 september 2026 via GitMCP voor python/cpython.

Gebruik `with open(...) as f:`. Het bestandsobject is een contextmanager: bij het verlaten van het blok wordt het bestand gesloten, ook wanneer in dat blok een exception ontstaat. Je hoeft daarvoor niet zelf een `try/finally` met `close()` te schrijven. Sluiten is niet hetzelfde als de fout afhandelen: een bestandscontextmanager laat de exception doorgaan naar een eventuele buitenste `except`.

```python
# Voorwaarde: voorbeeld.txt bestaat en is leesbaar als UTF-8.
try:
    with open("voorbeeld.txt", encoding="utf-8") as f:
        raise ValueError("Opzettelijke proeffout")
except ValueError:
    print(f.closed)  # True: gesloten voordat deze except wordt uitgevoerd.
```

Dit voorbeeld is niet uitgevoerd. De garantie betreft een exception tijdens het blok, niet bijvoorbeeld het abrupt beëindigen van het proces. Als open() mislukt, wordt het blok niet betreden.

## Dienst en controle

Het primaire GitMCP-project is idosal/git-mcp (Ido Salomon); de software heeft Apache-2.0 als licentie. De juridische hostingexploitant is niet vastgesteld. GitMCP is de aanbieder van de documentatiedienst, niet het Python-project; dit is geen vastgestelde officiële Python-MCP-dienst.

De primaire endpointpagina bevestigt https://gitmcp.io/python/cpython als MCP Server URL. Deze is aangesloten; de ontdekte catalogus bevat vier tools: documentatie ophalen, documentatie doorzoeken, repositorycode doorzoeken en URL-inhoud ophalen. Documentatie ophalen gaf de CPython-README. De semantische zoekactie vond geen relevante passage. Vervolgens leverde de URL-tool van dezelfde dienst de officiële Python-tutorial, die expliciet bevestigt dat with het bestand ook bij een exception sluit en f.closed als controle toont. Geen persoonlijke accounts, aankopen of publicatie gebruikt.

## Primaire bronnen

- GitMCP-documentatie en herkomst: https://github.com/idosal/git-mcp
- GitMCP-licentie: https://github.com/idosal/git-mcp/blob/main/LICENSE
- Gecontroleerde endpointpagina: https://gitmcp.io/python/cpython
- Officiële CPython-repository: https://github.com/python/cpython
- Via GitMCP gelezen Python-tutorial, onderdeel 7.2: https://docs.python.org/3/tutorial/inputoutput.html#reading-and-writing-files
- Bijbehorende primaire documentatiebron, gelinkt vanuit de tutorial: https://github.com/python/cpython/blob/main/Doc/tutorial/inputoutput.rst?plain=1
