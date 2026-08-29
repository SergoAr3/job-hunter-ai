# AI enrichment manual fixtures

This intentionally small, stable corpus is derived from sanitized vacancy text.
It never triggers real OpenAI requests in pytest. Each case holds a cleaned API
input and only the assertions useful for a later manual model comparison.

Run the opt-in comparison script with `OPENAI_API_KEY` and an optional
`OPENAI_MODEL`; inspect the printed outputs against `expect` before changing a
model default or prompt.
