"""Render the day's candidates into a single self-contained HTML page.

Everything is inlined — data, styles, script — for one specific reason: card
show venues have terrible reception, and a page that needs a network
round-trip is a page that fails when you're standing at the table. Once it's
cached it works with no signal at all.

tcgcsv also sets a restrictive CORS policy, so a browser can't fetch from it
directly even if the wifi were good. Pre-rendering is the only option.

    python scripts/page.py                    # newest alert file
    python scripts/page.py --date 2026-05-15 --out docs/index.html
"""

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DAILY_LIMIT, ROOT  # noqa: E402
from detect import ALERTS  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "index.html"

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0f172a">
<title>Spikes · __DATE__</title>
<link rel="manifest" href="data:application/manifest+json,__MANIFEST__">
<style>
:root {
  color-scheme: light dark;
  --bg: #f7f8fa; --card: #ffffff; --ink: #16181d; --muted: #6b7280;
  --line: #e4e7ec; --up: #0a7d34; --accent: #2563eb; --chip: #eef2f7; --warn: #a15c07;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115; --card: #171a21; --ink: #e8eaed; --muted: #9aa3af;
    --line: #262b35; --up: #3ddc84; --accent: #6ea8fe; --chip: #222733; --warn: #e0a34a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  -webkit-text-size-adjust: 100%;
}
header {
  position: sticky; top: 0; z-index: 5; background: var(--bg);
  border-bottom: 1px solid var(--line); padding: 14px 16px 10px;
}
h1 { margin: 0 0 2px; font-size: 19px; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 13px; }
.controls { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
input[type=search] {
  flex: 1 1 160px; min-width: 0; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--card); color: var(--ink); font-size: 15px;
}
button {
  padding: 7px 11px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--card); color: var(--ink); font-size: 13px; cursor: pointer;
}
button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
main { padding: 10px 12px 40px; max-width: 900px; margin: 0 auto; }
.row {
  display: grid; grid-template-columns: 56px 1fr auto; gap: 11px;
  align-items: start; background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; padding: 11px; margin-bottom: 9px;
}
.row.done { opacity: 0.42; }
.row img { width: 56px; border-radius: 6px; display: block; background: var(--chip); }
.name { font-weight: 600; line-height: 1.25; }
.name a { color: inherit; text-decoration: none; }
.name a:hover { text-decoration: underline; }
.meta { color: var(--muted); font-size: 12.5px; margin-top: 2px; }
.move { font-variant-numeric: tabular-nums; margin-top: 5px; }
.move b { color: var(--up); }
.net { color: var(--muted); font-size: 12.5px; }
.flags { margin-top: 5px; display: flex; flex-wrap: wrap; gap: 5px; }
.flag {
  font-size: 11.5px; padding: 2px 7px; border-radius: 99px;
  background: var(--chip); color: var(--muted); white-space: nowrap;
}
.flag.hot { color: var(--up); }
.flag.warn { color: var(--warn); }
.right { text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.pct { font-weight: 700; color: var(--up); font-variant-numeric: tabular-nums; white-space: nowrap; }
.spark { display: block; }
.check {
  border: 1px solid var(--line); background: var(--card); border-radius: 7px;
  width: 30px; height: 26px; font-size: 13px; line-height: 1; cursor: pointer; color: var(--muted);
}
.rank { font-size: 11px; color: var(--muted); }
.top { border-color: var(--accent); }
.empty { text-align: center; color: var(--muted); padding: 50px 20px; }
footer { text-align: center; color: var(--muted); font-size: 12px; padding: 0 16px 30px; }
@media (max-width: 430px) {
  .row { grid-template-columns: 46px 1fr; }
  .row img { width: 46px; }
  .right { grid-column: 1 / -1; flex-direction: row; align-items: center;
           justify-content: space-between; border-top: 1px solid var(--line); padding-top: 8px; }
}
</style>
</head>
<body>
<header>
  <h1>__HEADLINE__</h1>
  <div class="sub">__SUBHEAD__</div>
  <div class="controls">
    <input type="search" id="q" placeholder="Search card, set, or number…" autocomplete="off">
    <button id="f-all" aria-pressed="true">All</button>
    <button id="f-pokemon" aria-pressed="false">Pokémon</button>
    <button id="f-onepiece" aria-pressed="false">One Piece</button>
    <button id="f-cheap" aria-pressed="false">Under $10</button>
    <button id="f-top" aria-pressed="false">Alerted only</button>
    <button id="sort">Sort: $ gain</button>
  </div>
</header>
<main id="list"></main>
<footer>__DATE__ · generated by tcg-spike-tracker · prices from tcgcsv.com (TCGplayer)</footer>
<script>
const DATA = __DATA__;
const TOP = __TOP__;

const state = { game: "all", cheap: false, topOnly: false, q: "", sort: "gain" };
const done = new Set(JSON.parse(localStorage.getItem("done") || "[]"));
const money = n => "$" + n.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const idOf = c => c.product_id + "|" + c.sub_type_name;

function spark(history) {
  const points = history.filter(v => v !== null);
  if (points.length < 3) return "";
  const min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
  const width = 72, height = 22;
  let out = [], i = 0;
  for (const value of history) {
    if (value !== null) {
      const x = (i / (history.length - 1)) * width;
      const y = height - ((value - min) / span) * height;
      out.push(x.toFixed(1) + "," + y.toFixed(1));
    }
    i++;
  }
  return '<svg class="spark" width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + ' ' + height +
         '" fill="none" aria-hidden="true"><polyline points="' + out.join(" ") +
         '" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" opacity="0.65"/></svg>';
}

function flags(card) {
  const out = [];
  if (card.new_high) out.push(['hot', 'new high']);
  if (card.streak >= 3) out.push(['hot', card.streak + 'd climb']);
  if (card.low_state === "cheap") out.push(['hot', 'copies from ' + money(card.low)]);
  else if (card.low_state === "above") out.push(['warn', 'nothing under ' + money(card.low)]);
  else if (card.low !== null) out.push(['', 'low ' + money(card.low)]);
  return out.map(([cls, text]) => '<span class="flag ' + cls + '">' + text + '</span>').join("");
}

function render() {
  const needle = state.q.toLowerCase().trim();
  let rows = DATA.filter(card => {
    if (state.game !== "all" && card.game !== state.game) return false;
    if (state.cheap && card.baseline >= 10) return false;
    if (state.topOnly && !TOP.includes(idOf(card))) return false;
    if (needle) {
      const hay = (card.name + " " + card.set_name + " " + (card.set_abbreviation || "") + " " +
                   card.number + " " + (card.rarity || "")).toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
  rows.sort((a, b) => state.sort === "gain" ? b.gain - a.gain : b.pct - a.pct);

  const list = document.getElementById("list");
  if (!rows.length) { list.innerHTML = '<div class="empty">Nothing matches.</div>'; return; }

  list.innerHTML = rows.map(card => {
    const id = idOf(card);
    const isTop = TOP.includes(id);
    return '<article class="row' + (done.has(id) ? ' done' : '') + (isTop ? ' top' : '') + '" data-id="' + id + '">' +
      (card.image_url ? '<img loading="lazy" src="' + card.image_url + '" alt="">' : '<div></div>') +
      '<div>' +
        '<div class="name"><a href="' + card.url + '" target="_blank" rel="noopener">' + card.name + '</a></div>' +
        '<div class="meta">' + [card.set_abbreviation || card.set_name, card.number, card.rarity, card.sub_type_name]
            .filter(Boolean).join(" · ") + '</div>' +
        '<div class="move"><b>' + money(card.baseline) + ' → ' + money(card.price) + '</b> ' +
          '<span class="net">· ' + money(card.net_per_copy) + ' net/copy</span></div>' +
        '<div class="flags">' + flags(card) + '</div>' +
      '</div>' +
      '<div class="right">' +
        '<div class="pct">+' + Math.round(card.pct * 100) + '%</div>' +
        spark(card.history) +
        '<button class="check" title="Mark as handled">' + (done.has(id) ? '↩' : '✓') + '</button>' +
      '</div>' +
    '</article>';
  }).join("");
}

document.getElementById("list").addEventListener("click", event => {
  const button = event.target.closest(".check");
  if (!button) return;
  const id = button.closest(".row").dataset.id;
  done.has(id) ? done.delete(id) : done.add(id);
  localStorage.setItem("done", JSON.stringify([...done]));
  render();
});

document.getElementById("q").addEventListener("input", event => {
  state.q = event.target.value; render();
});

function press(id, on) { document.getElementById(id).setAttribute("aria-pressed", on ? "true" : "false"); }

for (const game of ["all", "pokemon", "onepiece"]) {
  document.getElementById("f-" + game).addEventListener("click", () => {
    state.game = game;
    for (const other of ["all", "pokemon", "onepiece"]) press("f-" + other, other === game);
    render();
  });
}
document.getElementById("f-cheap").addEventListener("click", () => {
  state.cheap = !state.cheap; press("f-cheap", state.cheap); render();
});
document.getElementById("f-top").addEventListener("click", () => {
  state.topOnly = !state.topOnly; press("f-top", state.topOnly); render();
});
document.getElementById("sort").addEventListener("click", event => {
  state.sort = state.sort === "gain" ? "pct" : "gain";
  event.target.textContent = "Sort: " + (state.sort === "gain" ? "$ gain" : "% move");
  render();
});

render();
</script>
</body>
</html>
"""


def render(report: dict) -> str:
    candidates = report.get("candidates") or []
    top_ids = [f"{a['product_id']}|{a['sub_type_name']}" for a in report.get("alerts", [])]

    by_game = {}
    for card in candidates:
        by_game[card["game"]] = by_game.get(card["game"], 0) + 1
    mix = ", ".join(f"{count} {game}" for game, count in sorted(by_game.items()))

    headline = f"{len(candidates)} candidate{'s' if len(candidates) != 1 else ''}"
    subhead = (
        f"{report['date']} · top {min(len(top_ids), DAILY_LIMIT)} alerted"
        + (f" · {mix}" if mix else "")
        + (f" · {report['suppressed_count']} suppressed as repeats"
           if report.get("suppressed_count") else "")
    )

    manifest = json.dumps(
        {
            "name": "TCG Spikes",
            "short_name": "Spikes",
            "display": "standalone",
            "background_color": "#0f1115",
            "theme_color": "#0f172a",
        },
        separators=(",", ":"),
    )

    return (
        TEMPLATE.replace("__DATA__", json.dumps(candidates, separators=(",", ":")))
        .replace("__TOP__", json.dumps(top_ids, separators=(",", ":")))
        .replace("__HEADLINE__", html.escape(headline))
        .replace("__SUBHEAD__", html.escape(subhead))
        .replace("__DATE__", html.escape(report["date"]))
        .replace("__MANIFEST__", manifest.replace('"', "%22").replace(" ", "%20"))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to newest alert file")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    files = sorted(ALERTS.glob("*.json"))
    if not files:
        raise SystemExit("no alert files — run scripts/detect.py first")
    path = ALERTS / f"{args.date}.json" if args.date else files[-1]
    if not path.exists():
        raise SystemExit(f"no alert file for {args.date}")

    report = json.loads(path.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(report), encoding="utf-8")

    size = args.out.stat().st_size / 1024
    print(f"{report['date']}: {len(report.get('candidates', []))} candidates "
          f"-> {args.out} ({size:.0f} KB)")


if __name__ == "__main__":
    main()
