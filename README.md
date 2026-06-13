# matchup-thumbs

An HTTP service that generates sports matchup images — thumbnails (16:9), logos, and posters (2:3) — on the fly for arbitrary team pairings across many leagues.

Given `(league, away, home, kind, style)` it composites team logos and colors into an image and returns it. Outputs are deterministic and aggressively cached at every tier.

All team metadata and logos come from public ESPN APIs/CDN — no paid services. Intended for personal/homelab use.

## Quick Start

```bash
cp .env.example .env
docker compose up
curl http://localhost:8000/healthz
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy --strict src/matchup_thumbs
```

## Legal

See [LICENSE](LICENSE) (MIT) and [DISCLAIMER.md](DISCLAIMER.md) for trademark attribution and ESPN sourcing notes.
