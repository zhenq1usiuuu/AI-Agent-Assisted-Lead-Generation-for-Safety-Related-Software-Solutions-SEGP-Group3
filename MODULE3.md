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
hypothesis. Add `--json` to print the complete combined JSON instead.
`--out` refuses to overwrite an existing file.

If the input has no `filter_score` object, Module 2 is calculated locally
before the second-model call. The reasoning output does not change scores,
gates, evidence, organisation names or quotations.
