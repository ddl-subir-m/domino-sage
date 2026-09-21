# Native state boundary experiment — #479

The four completed OpenCode runs are recorded in
`docs/research/479-opencode-{messages,responses,chat,transition}.json`. The direct
gateway evidence is in `docs/research/479-native-gateway-cycles.json`.

Install the pinned codec packages with `npm ci --ignore-scripts` in this folder.
Use the repository's pinned OpenCode 1.18.4 binary, not a global binary.
Use a Python environment with `httpx` and `python-dotenv`.

After taking the repository test slot, run `probe.py` with:

```text
python probe.py --binary /absolute/path/to/node_modules/.bin/opencode \
  --env /absolute/path/to/backend/.env --protocol messages \
  --output /tmp/sage-479-messages.json
```

Repeat for `responses` and `chat`. Add `--mux /absolute/path/to/mux.mjs` to test
the stable provider boundary. Add `--transition` to force repeated Messages →
Responses changes between inferences. This sequence is a codec experiment;
it is not the implementation of Sage's Auto and rescue policy.

The probe creates scratch XDG config, data, cache and state directories. It uses
the v1 prompt path and holds the existing machine OpenCode lock through all
server lifetimes. It makes two turns, restarts OpenCode, resumes, compacts, then
makes another turn. It keeps gateway credentials in the recorder process.

The JSON record retains request field names, state counts, usage and event types.
Inspect both outbound and replayed state. Successful final text alone does not
prove state retention. The scratch OpenCode database contains the synthetic
conversation and its native state; treat it as private and do not commit it.

`production_probe.py` uses the real Sage HTTP controls, route resolver, policy and
provider module. Its isolated project fixture comes from the HTTP test fixture;
Alias discovery and model calls use the deployed gateway. Run from a development
checkout with backend test dependencies installed:

```text
python spikes/native-reasoning/production_probe.py \
  --env /absolute/path/to/backend/.env \
  --binary /absolute/path/to/node_modules/.bin/opencode \
  --output /tmp/sage-native-production.json
```

It verifies Chat on all three protocols, the production compaction entry point,
an Auto write transition and rescue, and a harness-verified child session. It uses
only synthetic scratch files. Its model-call recorder retains shapes, tool names,
usage and settings, not text or opaque values. The transient project and OpenCode
database remain in a private temporary directory.
