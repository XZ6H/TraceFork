# tracefork-openai

OpenAI adapter for TraceFork: routes OpenAI Responses API calls through the
boundary runtime so they can be recorded and replayed. Supports sync and
async clients, records usage/latency metadata, and replays SDK response
objects (not raw dicts), including streaming event sequences.
