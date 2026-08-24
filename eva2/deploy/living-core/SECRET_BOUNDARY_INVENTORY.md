# Secretgrensinventaris

Deze inventaris voorkomt dat de Home Assistant-vertical als algemene
secret-hardening wordt gepresenteerd.

## Binnen deze vertical gehard

- `bootstrap/secrets.py`: één generieke, niet-blokkerende file-snapshot met
  absolute-pathcontrole, `lstat`, `O_NOFOLLOW`, `O_NONBLOCK`, fd-identiteit,
  exacte owner, owner-readbit, nul group/other-bits, 64 KiB-limiet en een
  post-readvergelijking van device/inode/uid/mode/size/ctime.
- Living Core Home Assistant: accepteert alleen
  `EVA_HOME_ASSISTANT_TOKEN_FILE`; de inline variabele wordt hard geweigerd.
- De overige bestaande Living Core-routes die al `environment_secret()`
  gebruikten (database, outbound Telegram en Stripe) profiteren van dezelfde
  filecontrole, maar behouden hun bestaande env/filekeuze.
- AgentMail in de Living Core leest zijn API-key nu via dezelfde harde
  fileloader. De huidige Compose-set bevat echter nog geen complete
  AgentMail-secretmount; dat deploymentdeel blijft dus open.
- `operations/telegram_ingress.py`: database-DSN, bot-token en
  principal-bindings gaan nu elk door dezelfde harde fileloader. Het
  telegram-native Compose-profiel bindt alleen expliciete, read-only hostfiles
  met `create_host_path: false`; Docker Compose `secrets:file` wordt daar niet
  gebruikt omdat de effectieve owner/mode niet betrouwbaar kan worden gezet.
- `operations/durable_goal_worker.py`: gebruikt uitsluitend
  `EVA_DATABASE_DSN_FILE` via dezelfde harde loader; het aparte profiel bindt
  de runtime-owned DSN read-only met `create_host_path: false`.
- `operations/install_schema.py`: gebruikt uitsluitend
  `EVA_DATABASE_DSN_FILE` via dezelfde harde loader; het aparte
  schema-installatieprofiel bindt de runtime-owned DSN read-only met
  `create_host_path: false`.

## Niet door deze vertical gedekt en dus deploymentblocker

- De legacy `stack.compose.yml`-migratieservice gebruikt nog Docker
  `secrets:file` en volgt dus niet het file-owner/modecontract van de geharde
  installer. Alleen het aparte schema-installatieprofiel volgt dat contract;
  de stack is geen beheerd B-releasepad.
- `operations/model_credentials.py`: afzonderlijke credentialauthority-reader
  en leasepublisher; deze heeft eigen begrenzing maar nog niet hetzelfde
  fd-/permissioncontract.
- `adapters/model/conversation.py`: afzonderlijke roterende modellease en
  JSON-validatie; deze reader is niet door deze vertical gemigreerd.
- PostgreSQLs `POSTGRES_PASSWORD_FILE` wordt door de upstream Postgres-image
  gelezen en niet door Eva-code.

De overige `read_text()`/`read_bytes()`-locaties zijn identitypacks,
fixture-public-keys, repository/workspacebestanden, CAS-objecten of
operatorreceipts en zijn geen credentialfile-routes. Dat onderscheid is alleen
een inventarisclaim; het maakt die bestandsgrenzen niet automatisch veilig voor
een ander doel.

Deze losse procesroutes mogen niet als algemeen gehard of deployklaar worden
geclaimd. Zij vereisen elk een afzonderlijk gereviewde migratie naar de harde
loader of een gelijkwaardig proces-specifiek contract. De huidige plan-only
B-preflight geeft daarom geen deploymenttoestemming.
