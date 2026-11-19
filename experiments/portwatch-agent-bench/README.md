# portwatch-agent-bench

A Python 3.11 CLI for Jinesis Lab. It measures whether an independent verifier produces fewer unsupported shipment updates than an agent reviewing its own answer at the same two-call budget.

**Live comparison pending.** Ollama was unavailable in the build environment. The fixtures and scoring were checked offline on Python 3.11.

## Run

From the directory containing the three files, run the offline checks with Python 3.11.

```sh
python benchmark.py --offline
```

Start Ollama in a separate terminal if needed.

```sh
ollama serve
```

Pull the default model and run the smoke case followed by the full benchmark.

```sh
ollama pull qwen2.5:1.5b
python benchmark.py --smoke
python benchmark.py
```

To reuse another small installed model, run `ollama list` and pass its tag with `--model`. The CLI uses only the Python standard library and sends requests to `http://127.0.0.1:11434/api/chat`.

## What the benchmark compares

Both workflows make two separate calls per case with the same model tag, shipment evidence, policy, output schema, temperature zero, seed 42, and 128-token output limit. Their first requests are identical.

- **Decide → self-review.** The second call receives the first answer as the assistant's own previous turn and reviews it against the policy.
- **Propose → independent verification.** The second call starts a fresh conversation and treats the proposal as data. It is asked to decide from the shipment evidence before comparing its decision with the proposal.

The full run includes six pairs and makes 48 chat calls. Each pair changes one fact about freshness, confirmation, a conflict, shipment identity, report source, or an active hold. The smoke case makes four separate calls. The expected decisions and citation labels were written before running, and each request includes only the evidence and workflow instructions plus the first answer when applicable. Which workflow runs first alternates by case.

## Metrics and results

Metrics use the final answer from each workflow. It must provide `decision` (`update`, `defer`, or `escalate`), `status` (the new status for an update and `null` otherwise), and `evidence_ids` (an array of event IDs).

- **Correct decisions** match the labeled decision and status.
- **Unsupported updates** are valid updates that conflict with the labeled decision or status, omit required supporting event IDs, or cite unknown event IDs. A correct action and status can still count as an unsupported update.
- **Unnecessary escalations** occur when the label calls for an update or defer.
- **Invalid outputs** fail JSON parsing or the response shape checks. The first answer's validation is saved separately so you can see whether the second call recovered.
- **Median latency** is the median per-case sum of the two client-measured request durations for that workflow.

The CLI writes one JSONL record per case with both workflows, both raw and parsed answers, validation errors if any, the expected label, scores, and latency. A successful smoke run writes `smoke-results.jsonl`. A successful full run writes `results.jsonl` and prints the difference in unsupported updates. Each successful rerun replaces its corresponding file. A connection or Ollama API failure exits with an error and preserves any prior file.

Read the unsupported-update counts alongside decision accuracy and invalid outputs. A workflow that defers every case could have zero unsupported updates while missing every valid update. The result describes these fixtures and the chosen model.

## Interview example

In the freshness pair, a confirmed carrier report says the shipment was delivered. At 120 minutes it supports an update to `delivered` citing `E-101`. At 121 minutes the same report is stale, so the correct decision is `defer`. If the draft updates both shipments and the independent verifier corrects only the stale case while self-review leaves it unchanged, that would be one fewer unsupported update with the same call budget.
