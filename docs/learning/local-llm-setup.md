# Local LLM setup (Ollama)

The trading-intel system uses **Ollama** for all LLM and embedding work. This means no API costs and no usage limits — but you need to run the models on your own machine.

## Why local?

- **Free.** No per-token cost. No monthly cap.
- **Private.** Your trading data and PDFs never leave your machine.
- **Offline-capable.** Once models are downloaded, no network needed.
- **Trade-off:** slower than Claude API and lower quality (especially for nuanced synthesis). Mitigated by choosing the largest model your hardware can run.

## Hardware requirements

| Model size | RAM needed (CPU) | VRAM needed (GPU) | Speed |
|---|---|---|---|
| 7B params | 8 GB | 6 GB | Fast |
| 14B params | 16 GB | 10 GB | Medium |
| 32B params | 32 GB | 20 GB | Slow on CPU, fine on GPU |
| 70B params | 64 GB+ | 48 GB | Very slow on CPU, needs high-end GPU |

**Recommended setup for trading-intel:**
- Minimum: 16 GB RAM, any modern CPU → run `qwen2.5:7b` for everything
- Comfortable: 32 GB RAM + 8 GB GPU → run `qwen2.5:14b` for daily, `qwen2.5:32b` for weekly
- Power user: 64 GB RAM + 24 GB GPU (RTX 4090, etc.) → run `qwen2.5:32b` daily, `llama3.3:70b-instruct-q4_K_M` weekly

## Installation (Windows)

1. **Download Ollama installer:** https://ollama.com/download/windows
2. **Run the installer** (`OllamaSetup.exe`)
3. **Verify it's running:** open PowerShell and run:
   ```
   ollama --version
   ```
   It auto-starts a service at `http://localhost:11434`.

## Pull the models trading-intel uses

Open PowerShell and run these one at a time (each downloads several GB):

```
ollama pull qwen2.5:7b        # for chunk tagging — fast
ollama pull qwen2.5:14b       # for daily AM summary
ollama pull qwen2.5:32b       # for weekly themes synthesis (optional — large)
ollama pull nomic-embed-text  # for embeddings (small, ~270 MB)
```

If you only have 16 GB RAM, skip `qwen2.5:32b` and override `LLM_WEEKLY_MODEL=qwen2.5:14b` in `.env`.

## Test it works

```
ollama run qwen2.5:14b "Summarize the FOMC's role in one sentence."
```

You should see a response in a few seconds. Type `/bye` to exit.

For embeddings:

```
ollama show nomic-embed-text
```

Confirms the model is installed.

## Model recommendations by use case

| Use case in trading-intel | Recommended model | Fallback if low RAM |
|---|---|---|
| Daily AM summary (`LLM_DAILY_MODEL`) | `qwen2.5:14b` | `qwen2.5:7b` |
| Weekly theme synthesis (`LLM_WEEKLY_MODEL`) | `qwen2.5:32b` or `llama3.3:70b` | `qwen2.5:14b` |
| PDF chunk tagging (`LLM_TAGGING_MODEL`) | `qwen2.5:7b` | same |
| Embeddings (`EMBEDDING_MODEL`) | `nomic-embed-text` | `mxbai-embed-large` |

Why Qwen 2.5? On the LMSys arena and HuggingFace's open-LLM leaderboard, Qwen 2.5 consistently outperforms Llama 3.1 of equivalent size for analytical/reasoning tasks. Llama 3.3 70B is the top open model overall but needs serious hardware.

## Alternative: switch back to paid Claude API later

If you decide later that local quality isn't enough, the `synthesis/` and `memory/embeddings/` layers will sit behind Protocols. Switching is one config change + one `pip install anthropic` away. Don't sweat the lock-in.

## Troubleshooting

**Ollama isn't responding:**
- Check the service: open Task Manager → Services → look for `ollama`. Restart if stopped.
- Test the port: `curl http://localhost:11434/api/tags` — should return JSON.

**Model is too slow:**
- Switch to a smaller variant (`qwen2.5:7b` instead of `14b`).
- Use quantized versions (e.g., `qwen2.5:14b-instruct-q4_K_M` is faster than the default).
- If you have an NVIDIA GPU, make sure Ollama is using it: `ollama ps` shows GPU usage.

**Out of memory:**
- Set `OLLAMA_NUM_PARALLEL=1` env var to disable parallelism.
- Use a smaller model.

**Embedding dimension mismatch:**
- If you switch embedding models, the dimension changes. Update `EMBEDDING_DIM` in `.env` and regenerate any existing embeddings.

## Performance expectations

On a Ryzen 7 + 32 GB RAM + RTX 4070:
- `qwen2.5:7b` — ~50 tokens/sec — AM summary in ~15 seconds
- `qwen2.5:14b` — ~25 tokens/sec — AM summary in ~30 seconds
- `qwen2.5:32b` — ~8 tokens/sec — weekly synthesis in 2–3 minutes

That's plenty for our cadence (one AM summary per morning, one weekly synthesis per week).

## When to upgrade to Claude API

Switch from Ollama to Claude API if:
- The AM summaries are routinely missing important context (subjective quality issue)
- You need the system to run on the DO droplet (Ollama needs GPU, droplet has none — would need an Ollama service elsewhere or move to API)
- Your usage exceeds ~5 million tokens/month (at which point API is no longer "cheap")

Until then, local is the right call.
