# DBOS-kandidaat lokaal uitvoeren

De kandidaat heeft één episode-ingang. Die schrijft uitsluitend een canonieke
`EpisodeOutcome`- of terminale `EpisodeFailureOutcome`-JSON naar stdout en geeft bij een
systeemfout een niet-nul exitcode:

```bash
state_dir=$(mktemp -d /tmp/eva2-dbos-state.XXXXXX)
./dbos/prepare_state.sh "$state_dir"
python dbos_candidate.py \
  --state-dir "$state_dir" \
  --fixture http://127.0.0.1:18765 \
  --contract contract/episode-noor-v1.json
./dbos/cleanup_state.sh "$state_dir"
```

`prepare_state.sh` gebruikt exact
`docker.io/library/postgres@sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50`.
De PostgreSQL-datadir staat duurzaam in `STATE_DIR/postgres-data`; manifest, credentials en
password-file zijn eigenaar-only en cryptografisch aan elkaar en aan de containerlabels
gebonden. Het password staat niet in de Docker-omgeving.

Een verdwenen container wordt door hetzelfde prepare-commando met dezelfde image,
credentials en datadir herbouwd. Voor een expliciete restoreproef verwijdert
`remove_container.sh STATE_DIR` alleen de gecontroleerde container en bewaart het de state.
`cleanup_state.sh STATE_DIR` is de definitieve cleanup en wist na labelcontrole ook credentials
en datadir. `run_local_scenarios.sh` bewijst happy path, Retry-After, remote accept plus
workercrash, PostgreSQL remove/recreate plus workflowherstel, en een definitieve reject met
call-loze terminale replay. Dezelfde matrix voert ook het tweede Mira-identity-pack op een
volledig schone state uit, zonder kandidaat- of configuratiepatch.
