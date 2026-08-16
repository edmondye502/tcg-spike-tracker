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
from analysis import BASELINE_WINDOW, RULE_SETS  # noqa: E402
from config import (  # noqa: E402
    CHEAP_PRICE_CEILING,
    DAILY_LIMIT,
    MARKETPLACE_FEE_RATE,
    RESERVED_CHEAP_SLOTS,
    ROOT,
    SHIPPING_COST,
    SUPPRESS_DAYS,
)
from detect import ALERTS, SPARKLINE_DAYS  # noqa: E402

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
.links { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 6px; }
.links a {
  font-size: 12px; padding: 3px 9px; border-radius: 99px; text-decoration: none;
  border: 1px solid var(--line); color: var(--accent); white-space: nowrap;
}
.links a:hover { border-color: var(--accent); }
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
details.guide {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  margin-bottom: 12px; font-size: 13.5px;
}
details.guide > summary {
  cursor: pointer; padding: 10px 13px; color: var(--muted); font-size: 13px;
  list-style: none; user-select: none;
}
details.guide > summary::-webkit-details-marker { display: none; }
details.guide > summary::before { content: "▸ "; }
details.guide[open] > summary::before { content: "▾ "; }
details.guide[open] > summary { border-bottom: 1px solid var(--line); }
.guide-body { padding: 4px 14px 14px; }
.guide-body h3 {
  font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--muted); margin: 16px 0 7px;
}
.guide-body h3:first-child { margin-top: 8px; }
.guide-body p { margin: 7px 0; color: var(--ink); }
.guide-body dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 6px 11px; align-items: baseline; }
.guide-body dt { justify-self: start; }
.guide-body dd { margin: 0; color: var(--muted); }
.guide-body table { border-collapse: collapse; width: 100%; margin: 4px 0; }
.guide-body th, .guide-body td {
  text-align: left; padding: 4px 9px 4px 0; border-bottom: 1px solid var(--line);
  font-variant-numeric: tabular-nums;
}
.guide-body th { color: var(--muted); font-weight: 600; font-size: 12px; }
.guide-body .tag-demo { display: inline-block; }
@media (max-width: 430px) {
  .guide-body dl { grid-template-columns: 1fr; gap: 2px; }
  .guide-body dd { margin-bottom: 8px; }
}
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
    <button id="f-top" aria-pressed="false">Top picks</button>
    <button id="f-new" aria-pressed="false">Hide repeats</button>
    <button id="sort">Sort: $ gain</button>
  </div>
</header>
<main>
__GUIDE__
<div id="list"></div>
</main>
<footer>__DATE__ · generated by tcg-spike-tracker · prices from tcgcsv.com (TCGplayer)</footer>
<script>
const DATA = __DATA__;
const TOP = __TOP__;

const state = { game: "all", cheap: false, topOnly: false, newOnly: false, q: "", sort: "gain" };
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
  if (card.repeat) out.push(['', 'already flagged ' + card.last_alerted]);
  if (card.new_high) out.push(['hot', 'new high']);
  if (card.streak >= 3) out.push(['hot', card.streak + 'd climb']);
  // "any cond." is not a hedge — tcgcsv's lowPrice is explicitly the lowest
  // listing regardless of condition, so the cheapest copy may be played.
  if (card.low_state === "cheap") out.push(['hot', 'listings from ' + money(card.low) + ' (any cond.)']);
  else if (card.low_state === "above") out.push(['warn', 'nothing under ' + money(card.low) + ' (any cond.)']);
  else if (card.low !== null) out.push(['', 'low ' + money(card.low) + ' (any cond.)']);
  return out.map(([cls, text]) => '<span class="flag ' + cls + '">' + text + '</span>').join("");
}

function render() {
  const needle = state.q.toLowerCase().trim();
  let rows = DATA.filter(card => {
    if (state.game !== "all" && card.game !== state.game) return false;
    if (state.cheap && card.baseline >= 10) return false;
    if (state.topOnly && !TOP.includes(idOf(card))) return false;
    if (state.newOnly && card.repeat) return false;
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
        '<div class="links">' +
          '<a href="' + card.url + '" target="_blank" rel="noopener">TCGplayer</a>' +
          '<a href="' + card.ebay_url + '" target="_blank" rel="noopener">eBay ↗</a>' +
          '<a href="' + card.ebay_sold_url + '" target="_blank" rel="noopener">sold comps ↗</a>' +
        '</div>' +
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
document.getElementById("f-new").addEventListener("click", () => {
  state.newOnly = !state.newOnly; press("f-new", state.newOnly); render();
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


def build_guide(report: dict) -> str:
    """The collapsible explainer, generated from the live config.

    Built from the same constants the detector actually uses, so the page
    can't quietly drift out of sync with the rules it's describing.
    """
    bands = RULE_SETS.get(report.get("rules", "loose"), RULE_SETS["loose"])
    band_rows = "".join(
        f"<tr><td>${low:,.0f} – ${high:,.0f}</td>"
        f"<td>+{pct:.0%} and at least ${dollar:,.2f}</td></tr>"
        for low, high, pct, dollar in bands
    )
    fee_pct = f"{MARKETPLACE_FEE_RATE:.2%}".rstrip("0").rstrip(".")

    return f"""<details class="guide">
<summary>How cards are picked, and what the tags mean</summary>
<div class="guide-body">

<h3>How a card gets here</h3>
<p>Every day, each card's market price is compared against the
<b>median of the previous {BASELINE_WINDOW} days</b> — a median rather than
yesterday's price, so one odd day can't fake a spike. A card is listed if the
jump clears the bar for its price range:</p>
<table>
<tr><th>Price before</th><th>Needs to rise by</th></tr>
{band_rows}
</table>
<p>Cheap cards need a big multiple to be worth the trip; expensive cards are
already repriced by dealers, so they need a larger dollar move. Cards under $1
and over ${bands[-1][1]:,.0f} are ignored, along with sealed product and sets
older than four years.</p>

<h3>Top picks (the highlighted ones)</h3>
<p>A blue border marks the day's <b>{DAILY_LIMIT} top picks</b>. Everything
else cleared the bar but didn't make the cut — still worth a look, just
ranked lower.</p>
<p>Picks are ranked by dollar gain, except that
<b>{RESERVED_CHEAP_SLOTS} slots are held back for cards under
${CHEAP_PRICE_CEILING:,.0f}</b>. Without that, big-money cards would take
every slot and the cheap binder finds — the ones most likely to still be
sitting somewhere underpriced — would never surface. Reserved slots that go
unused fall through, so the list always fills.</p>
<p>Anything flagged in the last {SUPPRESS_DAYS} days is kept out of the picks
so a card mid-run doesn't take a slot every day. It stays in the list below,
marked.</p>

<h3>Tags</h3>
<dl>
<dt><span class="flag">already flagged …</span></dt>
<dd>Came up within the last {SUPPRESS_DAYS} days, so it can't be a top pick.
Use <b>Hide repeats</b> to see only what's new since you last looked.</dd>

<dt><span class="flag hot">new high</span></dt>
<dd>Highest price in the tracked window. Separates a real breakout from a card
bouncing back to a level it's already been.</dd>

<dt><span class="flag hot">5d climb</span></dt>
<dd>Rose that many days in a row (only shown at 3+). A steady climb tends to
be a safer buy than a one-day pop — the sharpest single-day spikes are the
ones that fall back hardest.</dd>

<dt><span class="flag hot">listings from $85.00 (any cond.)</span></dt>
<dd>The cheapest listing is 25% or more below market. Promising — but read
the condition note below before you act on it.</dd>

<dt><span class="flag warn">nothing under $99.98 (any cond.)</span></dt>
<dd>The cheapest listing is <i>above</i> market price, so you can't buy at
market right now. Common on pricier cards, where nobody lists cheap.</dd>

<dt><span class="flag">low $46.00 (any cond.)</span></dt>
<dd>Cheapest listing sits just under market. Neutral.</dd>
</dl>

<h3>Numbers</h3>
<dl>
<dt><b>$105.60 → $124.09</b></dt>
<dd>The {BASELINE_WINDOW}-day median, then today's market price.</dd>
<dt><b>+18%</b></dt>
<dd>The move against that median — not against yesterday.</dd>
<dt><b>$107.27 net/copy</b></dt>
<dd>Roughly what you'd clear selling one, after {fee_pct} marketplace fees and
${SHIPPING_COST:,.2f} shipping.</dd>
<dt><b>The line chart</b></dt>
<dd>{SPARKLINE_DAYS} days of market price. Tells you whether this is a first
breakout or a card that just oscillates.</dd>
<dt><b>✓</b></dt>
<dd>Marks a card handled and greys it out. Saved in this browser only — it
survives updates but doesn't follow you to another device.</dd>
</dl>

<h3>Links</h3>
<dl>
<dt><b>TCGplayer</b></dt>
<dd>The product page these prices come from.</dd>
<dt><b>eBay</b></dt>
<dd>Active listings for this exact card, cheapest first including shipping.
Searched by name and collector number — that number is the most reliable way
to land on the right printing.</dd>
<dt><b>sold comps</b></dt>
<dd>Recently <i>sold</i> listings, newest first. Worth checking before you
buy: this data has no sales volume in it, so a spike can happen on very few
sales. If eBay shows copies actually selling near the new price, the move is
real. If nothing has sold, be careful.</dd>
</dl>
<p>eBay searches match on title text, so an unusual printing occasionally
returns too much or too little. Trim a word or two if the results look off.</p>

<h3>About condition — read this one</h3>
<p><b>None of these prices are Near Mint specific.</b> The source aggregates
every condition together: the low price is, in its own words, "the lowest
listed price of a card sans condition," so the cheapest listing behind a
<span class="flag hot">listings from …</span> tag can easily be a played
copy. Market price "will roughly center around Near Mint or Lightly Played
listings, but not always."</p>
<p>Condition-level prices need SKU-level access, which this free source
doesn't carry. In practice: treat the low price as a floor across all
conditions, and check the actual listing or the sold comps before assuming
you can buy or sell an NM copy at it. Only sets from the last four years are
tracked, which helps — the worst condition mixing shows up in older cards.</p>

<h3>Worth knowing</h3>
<p>About 70% of cards don't reprice on a given day — TCGplayer only moves
market price when something actually sells. And nothing in this data shows
sales volume, so a move can happen on very few sales. Sold comps on eBay are
the fastest way to confirm a price is real.</p>

</div>
</details>"""


def render(report: dict) -> str:
    candidates = report.get("candidates") or []
    top_ids = [f"{a['product_id']}|{a['sub_type_name']}" for a in report.get("alerts", [])]

    by_game = {}
    for card in candidates:
        by_game[card["game"]] = by_game.get(card["game"], 0) + 1
    mix = ", ".join(f"{count} {game}" for game, count in sorted(by_game.items()))

    headline = f"{len(candidates)} candidate{'s' if len(candidates) != 1 else ''}"
    subhead = (
        f"{report['date']} · {min(len(top_ids), DAILY_LIMIT)} top picks"
        + (f" · {mix}" if mix else "")
        + (f" · {report['suppressed_count']} repeat"
           f"{'s' if report['suppressed_count'] != 1 else ''} from earlier this week"
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
        .replace("__GUIDE__", build_guide(report))
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
