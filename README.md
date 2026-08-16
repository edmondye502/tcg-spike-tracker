# tcg-spike-tracker

Finds Pokémon and One Piece singles whose price jumped in the last day, so you
can go looking for copies that haven't been repriced yet — at a card show, or
in a listing someone hasn't updated.

Runs free: GitHub Actions cron, data from [tcgcsv.com](https://tcgcsv.com), a
Discord webhook, and a static page on GitHub Pages. No database service, no
API keys.

## How it works

1. **Fetch** — `backfill.py` pulls tcgcsv's daily price archive (~4 MB) and
   writes one compressed file per day covering Pokémon (category 3) and
   One Piece (68). Day files are append-only and committed to the repo.
2. **Detect** — `detect.py` compares each card's market price against the
   median of the previous 7 snapshots and flags moves that clear a
   price-banded threshold.
3. **Rank** — the day's candidates are cut to 10 top picks, with 4 slots held
   back for cards under $10. Cards flagged within the last 7 days stay off the
   picks but remain in the list, marked, so nothing disappears between visits.
4. **Deliver** — a self-contained HTML page with the full candidate list.
   Discord is optional and off unless you set a webhook.

## Setup

```bash
pip install -r requirements.txt
python scripts/backfill.py --days 180
python scripts/catalog.py
python scripts/db.py
python scripts/detect.py
python scripts/page.py
```

Then open `docs/index.html` — it's a single self-contained file and works
straight off the filesystem, no server needed.

### Running it automatically

The GitHub Actions workflow in `.github/workflows/daily.yml` does the same
thing every day at 20:35 UTC (tcgcsv publishes around 20:00) and commits the
updated page. Serve `docs/` from GitHub Pages to read it anywhere — note that
Pages requires a public repo on the free plan.

Committing on every run is also what keeps the schedule alive: GitHub disables
scheduled workflows after 60 days of repository inactivity.

Nothing is lost if it doesn't run. Because everything is rebuilt from tcgcsv's
archive rather than live snapshots, a missed week is recovered in full with
`backfill.py --days 10`. The archive goes back to 2024-02-08.

### Discord (optional)

Off by default. The workflow's notify step is skipped unless a
`DISCORD_WEBHOOK_URL` secret exists, so adding one later is the only change
needed to start posting.

Create a webhook in the target channel's settings, then put the URL in `.env`
locally (see `.env.example`) and in the repo's Actions secrets. Anyone holding
that URL can post to your channel, so it never belongs in the repo. Preview
without sending:

```bash
python scripts/notify.py --dry-run
```

A webhook post doesn't ping anyone by default — whether it notifies is purely
the channel's notification setting. Optionally set an Actions variable
`PAGE_URL` so each message links to the full list.

## What the data actually supports

Calibrated by replaying 180 days (2026-02-10 → 2026-08-08, 9.2M price rows,
34,414 singles) through candidate rule sets and checking what happened next.

**Spikes mostly stick.** Only 5% reverted within 7 days and 14% within 30.
Of cards that repriced again, 76% were still more than 25% above their
pre-spike baseline a month later, with a median move of +13%.

**Loose thresholds beat strict ones.** Tightening the bands bought ~10 points
of hold rate and cost ~85% of the volume — and the most violent spikes
mean-reverted hardest (the strict rule set went *negative* at 30 days while
loose stayed positive). So detection runs loose and the filtering happens at
the ranking step, where a cheap binder find can be weighed against a
big-dollar move instead of being discarded before it's seen.

**~70% of daily market prices are byte-identical.** TCGplayer only moves
market price when a sale happens, rising to 82% for cards over $150. Any
"did it hold" measurement has to exclude cards that simply never repriced,
or it mostly measures staleness.

**The cheapest listing is often above the market price** — 19% of the time
overall, and 54% of the time for cards over $150, because for thin cards
nobody lists cheap. Below $5 it's only 5%, so the "copies still listed cheap"
signal is meaningful exactly where you want it. Alerts label the two cases
differently (`copies from $X` vs `nothing under $X`) because they mean
opposite things.

**Cheap spikes are genuinely rare.** Cards in the $1–5 band are ~10% of
alerts no matter how the thresholds are set, because cheap cards are the ones
whose prices go stale. Roughly one every other day. That's why they get
reserved slots.

## Scripts

| script | does |
|---|---|
| `backfill.py` | download archives → `data/prices/YYYY-MM-DD.csv.gz` |
| `catalog.py` | set + card metadata → `data/catalog/` (singles only) |
| `db.py` | rebuild `data/spike.db` from day files (derived, gitignored) |
| `analysis.py` | shared detection primitives — bands, baselines, guards |
| `replay.py` | score rule sets against history |
| `detect.py` | today's spikes → `data/alerts/YYYY-MM-DD.json` |
| `notify.py` | post to Discord |
| `page.py` | render `docs/index.html` |

## Notes

- **Storage.** ~578 KB per day, so about 210 MB a year. The SQLite database
  is a derived artifact rebuilt from the day files, never committed — git
  stores a whole fresh copy of a modified binary in every commit, which is
  what makes the naive "commit the .db" approach fall over. When the repo
  eventually gets large, consolidating finished months into single files
  compresses several times further.
- **Gaps are handled explicitly.** A baseline window that reaches back across
  a missing day is skipped rather than scored — otherwise a failed cron run
  makes the entire catalog look like it spiked.
- **No sales volume exists in this data.** Nothing indicates whether ten
  copies sold or zero. The low-vs-market gap and the low-to-high spread are
  the available proxies.
- **Prices are not condition specific.** tcgcsv aggregates every condition:
  `lowPrice` is "the lowest listed price of a card sans condition", and
  `marketPrice` "will roughly center around Near Mint or Lightly Played
  listings, but not always". Near Mint pricing requires SKU-level access,
  which means your own TCGplayer API credentials. Alerts label low prices
  "any cond." rather than implying an NM copy is available at that price.
- **tcgcsv etiquette.** One person's Patreon-funded mirror. It requires a
  named User-Agent, asks for ~100 ms between requests, and updates once a
  day — there's no reason to poll more often. Its CORS policy also blocks
  browser fetches, which is part of why the page is pre-rendered.
