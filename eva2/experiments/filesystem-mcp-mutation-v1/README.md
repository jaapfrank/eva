# Officiële filesystem-MCP als wegwerp-mutatieprovider

Deze kandidaat gebruikt de onderhouden officiële
`@modelcontextprotocol/server-filesystem`-release `2026.8.31`. De provider
schrijft uitsluitend in een nieuw wegwerpvolume. `write_file` is de echte
mutatie; `read_text_file` is de afzonderlijke provider-readback. Geen van beide
functies staat in Eva's productcode.

IBM ContextForge `v1.0.8` zet de stdio-server alleen om naar Streamable HTTP.
Eva blijft eigenaar van modelkeuze, persoonsgrens, duurzame toelating,
herstelbesluit en bewijs. De provider blijft eigenaar van bestandstoestand en
de concrete toolimplementatie.

## Verzegelde bronnen

- npm: `@modelcontextprotocol/server-filesystem@2026.8.31`
- npm-integrity:
  `sha512-kKaFkyAh6oipvc9+EAbJ552JafnMnOq5nzmzWkp1jJdBhTAAGpmIpWihUG1+rfNhmEFM98gUZDdCHCDD4v6a7Q==`
- npm-tarball-SHA-256:
  `a239da270c403c42eb03e1ca7cca07c858085819797b74856ddbb91b50491b1a`
- ContextForge-image:
  `sha256:daf923c247fd569df5f8c9ee418cbc259fade564f78e0a0852937139a181c4dd`
- Node RPM: `nodejs-22.23.2-1.el10_2.x86_64` plus de exact gemanifesteerde
  transitieve RPM-set.

De Dockerfile heeft tijdens de build geen netwerk. Een afzonderlijke
artifactvoorbereiding materialiseert eerst de npm-cache en exacte RPM-bestanden
in een private, alleen-lezen artifactset. De releaseproef moet vervolgens met
`--network none --no-cache` opnieuw bouwen en het resulterende image scannen.

Dit is geen algemene bestandsbevoegdheid voor Eva en geen live provider. Het
volume bevat alleen een willekeurige bewijsmarker en wordt na de proef volledig
verwijderd.
