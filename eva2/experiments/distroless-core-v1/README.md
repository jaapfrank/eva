# Distroless Eva2-coreproef

Deze proef verandert Eva's code en Pythonruntime niet. Zij kopieert die uit de
offline gebouwde, schone managed-B-image naar één minimale, digest-gepinde
distroless Debian 12-basis. Van de 39 vastgezette runtimepackages vervangt zij
alleen `cryptography`; de andere packages blijven uit de bewezen image komen.

De proef is alleen een rebasekandidaat. Een groene import, runtimeproef en scan
zijn nodig voordat zij ergens als vervanger mag worden genoemd. De bronimage is
via de verplichte buildarg `PROVEN_CORE_IMAGE` aan een lokaal gecontroleerde
bronimage gebonden. De standaardwaarde blijft het historische checkpoint
`ceaaa7d81ef93be7a49d8bd53ce88d872ff94f77`; een nieuwe kandidaat moet de
exacte bronimage en image-ID in haar receipt vastleggen. Er wordt niets uit een
vuile werkmap gekopieerd en de build gebruikt geen netwerk.

De eerste distroless-start miste `libffi`; de databaseclient heeft daarnaast
`zlib` nodig. De distrolessvariant kopieert daarom uitsluitend `libffi.so.8`
en `libz.so.1` uit de gebonden Pythonbasis. Geen van beide packages staat in
de HIGH/CRITICAL-uitvoer van die apart gescande basis. De uiteindelijke
receipt bindt ook de hashes van deze bestanden, omdat een scanner losse
gekopieerde bibliotheken niet altijd aan hun oorspronkelijke package koppelt.
De Chainguardvergelijking was 0/0 als losse basis, maar bleek
ABI-incompatibel met Python 3.12's OpenSSL-module en is functioneel afgewezen.

`Dockerfile.source-refresh` maakt een latere codekandidaat zonder de bewezen
dependencylaag opnieuw te downloaden of installeren. De basis moet als exacte
lokale digest worden opgegeven. De context komt uitsluitend uit een schone
Git-archive en vervangt alleen Eva-bron, connector-SDK, migraties en schema's.
De Dockerfile bevat bewust geen `RUN`, package-manager, shell of netwerkstap.
Een bronrefresh telt pas na import-, migratie-, runtime- en 0/0-scanbewijs.

De resterende drie Pythonbevindingen komen allemaal uit `cryptography 46.0.5`.
De kandidaat installeert daarom uitsluitend de officiële abi3-wheel van
`cryptography 50.0.1`, gebonden aan SHA-256
`51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a`.
Dit blijft een dependencyproef. De productlock blijft ongewijzigd totdat ook
de vaste wheelhouse-, release-input- en rollbackstappen zijn gesloten.
