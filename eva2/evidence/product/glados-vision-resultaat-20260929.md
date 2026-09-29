# GLaDOS vision — afgebakend onderzoek

Nieuw publiek, alleen-lezen onderzoek van de tijdens deze opdracht opgehaalde main-versie; geen voorganger gebruikt. Geen installatie of praktijktest. Links verwijzen naar de veranderlijke main-tak; een exacte commit is niet vastgesteld.

## Beeldbronnen: implementatie
[VisionConfig](https://github.com/dnhkng/GLaDOS/blob/main/src/glados/vision/vision_config.py) accepteert camera_spec als niet-negatieve camera-index of tekenreeks voor URI/bestandsnaam; camera_index blijft een alias. [VisionProcessor](https://github.com/dnhkng/GLaDOS/blob/main/src/glados/vision/vision_processor.py) geeft dit rechtstreeks aan cv2.VideoCapture en leest frames. Daarmee zijn lokale camera’s en door de aanwezige OpenCV-backend ondersteunde streams/videobestanden voorzien. Het [voorbeeld](https://github.com/dnhkng/GLaDOS/blob/main/configs/glados_vision_config.yaml) noemt expliciet RTSP/IP-camera’s. Niet ieder protocol, codec of apparaat is daarmee gegarandeerd. Geen aparte schermopname-, browserbeeld- of foto-uploadroute aangetroffen in deze vision-code. FastVLM.encode_image accepteert intern wel een beeldarray; dat is geen kant-en-klare gebruikersinvoer.

## Starten en werking
De [handleiding](https://github.com/dnhkng/GLaDOS/blob/main/docs/vision.md) geeft: `uv run glados start --config ./configs/glados_vision_config.yaml`. Vereist zijn modelbestanden en een bruikbare beeldbron. Het voorbeeld bevat Glados.vision met camera_spec: 0, interval 5 seconden, drempel 0,05 en maximaal 64 tokens.

Belangrijk documentatieverschil: de handleiding noemt enabled: true/false en standaard 200 tokens; VisionConfig heeft geen enabled-veld en standaard 64. De [engine](https://github.com/dnhkng/GLaDOS/blob/main/src/glados/core/engine.py) maakt VisionProcessor bij aanwezige vision-configuratie; weglaten/null is de onderbouwde uitschakelroute, niet blind enabled: false gebruiken. Autonomie staat in het voorbeeld afzonderlijk uit.

De processor vergelijkt frames, slaat vrijwel onveranderde scènes over, genereert beschrijvingen en publiceert gebeurtenissen. Aanvragen voor gericht kijken pakken een frame, maar kunnen bij geringe verandering opgeslagen beeldkenmerken/antwoorden hergebruiken: niet iedere aanvraag doet volledige nieuwe inferentie.

## Model en hardware
De documentatie noemt Apple FastVLM-0.5B, distributie onnx-community/FastVLM-0.5B-ONNX, ongeveer 640 MB modelbestanden. [fastvlm.py](https://github.com/dnhkng/GLaDOS/blob/main/src/glados/vision/fastvlm.py) laadt vision_encoder_fp16.onnx, embed_tokens_int8.onnx en decoder_model_merged_q4f16.onnx plus configuratie/tokenizer lokaal. De code kiest CUDA met CPU-terugval, anders CPU; geen vereiste Apple-hardware. Geen betrouwbare minimale RAM/VRAM-eis vastgesteld; bestandsgrootte is geen werkgeheugeneis.

Let op: de downloadinstructie bewaart ONNX-bestanden onder onnx/, terwijl de gelezen loader ze rechtstreeks onder model_dir zoekt. Controleer de plaatsing; het commando alleen bewijst geen werkende start. De claim ‘85× sneller’ is documentatie, hier niet onafhankelijk gemeten.

## Blijven beelden lokaal?
In de gelezen processor/modelcode blijven frames en kenmerken in lokale verwerking; geen beeldupload of frame-opslag aangetroffen. Dat is geen volledige privacy-audit. Beschrijvingen worden wel gelogd en volgens de documentatie aan LLM-context toegevoegd. De [README](https://github.com/dnhkng/GLaDOS/blob/main/README.md) ondersteunt cloud-LLM’s: beeldinformatie in tekst kan dan vertrekken. Een netwerkcamera betekent bovendien netwerktransport van bronbeelden. ‘Alles blijft lokaal’ is dus te absoluut.

## Advies voor Eva — voorstel, geen resultaat
Interessante bouwstenen zijn lokale ONNX-inferentie, wijzigingsdetectie en een compacte scènebeschrijving. Beproef die alleen met expliciete cameratoestemming, bron/tijdstip, vervaltermijn en gescheiden toestemming voor acties. Meet herkenningsfouten, vertraging en geheugen op Eva’s hardware; uit deze bronlezing volgt geen bewezen kwaliteitsvoordeel boven Eva.
