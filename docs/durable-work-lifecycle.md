# Lifecycle van duurzaam werk

Voor episode-gebonden werk is `eva_goal_episodes.state` de gezaghebbende
levenscyclus. `eva_work_items.status` is een uitvoeringsprojectie: handig voor
planning en de werkinterface, maar niet een tweede bron van waarheid.

Alle gecombineerde overgangen lopen via `transitionGoalEpisode` in
`src/mind/portfolio.js`. Die schrijft de episode, de gekoppelde werkprojectie
en het transition-event in één SQLite-transactie. Een optionele
`idempotencyKey` maakt herhaling na een procesrestart een no-op. Bestaande
compatibiliteitsexports zoals `updateGoalEpisode` en `recordEpisodeDelivery`
delegeren aan dezelfde writer.

De projectie is volledig afleidbaar voor lifecycle-status:

| Episode | Werkprojectie |
| --- | --- |
| `committed`, `executing`, `waiting`, `recovering`, `verifying`, `delivery_pending`, `delivery_recorded` | `open` |
| `blocked` | `blocked` |
| `closed` | `done` |
| `abandoned` | `cancelled` |

`closed` en `abandoned` zijn monotoon terminaal. Een episode met
`delivery_policy = required` mag geen terminale toestand krijgen voordat er
een `eva_delivery_receipts`-regel bestaat. Receipt, sluiting en werkprojectie
worden atomair vastgelegd.

De grens is bewust smal. `eva_cognitive_episodes`, explorations en aspirations
zijn semantisch andere aggregates: respectievelijk denk-/gesprekscontinuïteit,
onderzoek en verlangens/doelvorming. Ze mogen naar duurzaam werk leiden, maar
hun eigen lifecycle wordt niet door deze writer overgenomen. Werk zonder
gekoppelde goal-episode blijft voorlopig via `updateWorkItem` werken voor
backwards compatibility; zodra het wordt gebonden, is de episode leidend.
