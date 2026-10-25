

<!-- LABEL_DICTIONARY:BEGIN -->
# Label dictionary

`feature_timestamp_utc`: historical signal timestamp.

`source_timestamp_utc`, `source_sha256`, `source_row`: current snapshot
provenance.

For each horizon h in 1, 5, 30, and 60 seconds:

- `target_timestamp_utc_{h}s`: feature timestamp plus exactly h seconds.
- `target_source_timestamp_utc_{h}s`: snapshot underlying the target state.
- `target_source_sha256_{h}s`, `target_source_row_{h}s`: target provenance.
- `target_snapshot_age_seconds_{h}s`: target time minus target source time.
- `target_mid_price_{h}s`: sampled target mid-price; an outcome diagnostic.
- `label_{h}s_pips`: forward mid-price difference divided by 0.0001;
  missing when invalid.
- `label_valid_{h}s`: both endpoint states and intervening price path pass.
- `label_reason_{h}s`: first applicable failure reason, or valid.
- `same_source_record_{h}s`: valid endpoints use identical source content
  and source row.
- `split_{h}s`: chronological role, purge status, or invalid-label status.

Future target and label fields must never enter model predictors.

The target state uses quotes observed at or before the exact target time.
No first-quote-after-target substitution is used.

Purging excludes labels whose target reaches or crosses their period end.
The pilot holdout is separate from the future full-history final test.

Displayed sizes are not executed trade volumes. This is a research
prototype. Predictive accuracy does not establish achievable trading profits.
<!-- LABEL_DICTIONARY:END -->
