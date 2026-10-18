# Sample repository for the cognous-ocs action

A stand-in for a user's repository, used by this repository's CI to run the composite action
(`.github/actions/ocs`). Nothing here is real: every file is synthetic.

- `workflow/`: byte-for-byte the output of `cognous-ocs init workflow` (a manifest, a policy, a sample run record, `workflow.json`).
- `evidence/evidence-pack.json`: byte-for-byte the evidence pack bundled with the customer-service example.

`scripts/check_examples_parity.py` fails if either drifts from its source.
