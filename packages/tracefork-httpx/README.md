# tracefork-httpx

httpx adapter for TraceFork: an httpx transport that routes requests through
the boundary runtime so traffic can be recorded and replayed hermetically.
Async-first (AsyncClient); secret headers are redacted before persistence.
