# LLM reasoning (Module 3)

Module 3 adds a second model after the deterministic Filter + score stage. The
existing Kimi client and scoring code are unchanged. The new stage reads a
result JSON, sends a compact scoring/evidence brief to the reasoning model,
and produces a human-readable explanation plus the original complete JSON.

## Models

- Stage model: Kimi K3 (`MOONSHOT_*` settings).
- Reasoning model: OpenAI `gpt-4o-mini` by default (`REASONING_*` settings).

The reasoning client is generic OpenAI-compatible. DeepSeek, Qwen, GLM or
another compatible `/chat/completions` endpoint can be used by changing
`REASONING_BASE_URL` and `REASONING_MODEL`. The stage refuses to run when the
second model is the same model on the same base URL as the Kimi stage.
Provider separation is recorded in the report because extraction and
reasoning have independent model and billing paths.

Copy the first- and second-model values from `.env.example` into the project
`.env` file. The new API key is never placed in prompts or result files.

## Run

First export a result with Filter + score:

```powershell
python -m app.cli --mode demo --prompt "Test research" --score --output scored.json
python -m app.reasoning scored.json --out reasoned.json
```

The default output is a readable report showing the overall summary, each
account recommendation, the reason, risks, supporting evidence and next-step
hypothesis. Recommendations are assigned by the code's fixed grade/gate
mapping; the model explains them but cannot choose them.

Deterministic validation checks generated text before it is trusted:

- quoted strings must match an exact source excerpt;
- mentioned scores and grades must match Module 2 values;
- forbidden claims such as `non-compliant` and `would have prevented` are
  rejected;
- the model retries up to three times, then the result is marked
  `needs_human_review` with the issues preserved.

Each account records its model, provider, token usage and an even cost share.
Set `REASONING_INPUT_COST_PER_MILLION` and
`REASONING_OUTPUT_COST_PER_MILLION` to override provider defaults. The
provider invoice remains authoritative.

Add `--json` to print the complete combined JSON instead. `--out` refuses to
overwrite an existing file.

If the input has no `filter_score` object, Module 2 is calculated locally
before the second-model call. The reasoning output does not change scores,
gates, evidence, organisation names or quotations.

Local testing exception: REASONING_USE_KIMI=true reuses the complete Moonshot configuration and permits the same model. This does not provide independent cross-model validation. See README.md for the four-module test commands.
