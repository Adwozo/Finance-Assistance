# Finance Assistance

A self-hosted personal finance assistant. Drop bank/credit-card statements into
a folder, the app ingests them, **intelligently categorizes** transactions during
conversion, detects transfers between your own accounts, handles investment
accounts correctly, and renders a Mint-style HTML dashboard with drill-down
spending analysis and an action-needed workflow. A local MCP server converts
arbitrary statement formats into a canonical CSV the app understands — and bakes
a best-guess category into every row at conversion time.

- **Stack:** Python 3.11, FastAPI, Jinja2, SQLAlchemy, TailwindCSS (CDN), Chart.js, HTMX
- **DB:** SQLite by default, PostgreSQL-compatible (no SQLite-specific SQL)
- **Locale:** Hong Kong — day-first dates (`DD/MM/YYYY`), default currency `HKD`
- **UI reference:** Mint / Monarch Money — sidebar accounts/categories, dashboard cards + charts, filterable transactions table

## Features

### Dashboard
- Total balance, this-month spending / income / net summary cards.
- Spending-by-category doughnut chart and a 6-month income/spending trend line.
- Recent-transactions list with transfer/investment flags and category-event badges.
- Investments card (portfolio value; this-month buys / sells / dividends / interest / fees).

### Transactions
- Filterable table (date range, account, category, transfer flag, free-text search).
- Inline category editing via a **searchable, tree-structured CategoryPicker**
  (color dots, depth indentation, type-to-filter).
- Per-row transfer toggle and delete.
- `category_event` badge on each row showing what drove the converter's guess
  (`merchant_pattern`, `lunar_new_year`, `uncertain`, …).

### Spending by Category (drill-down page)
- Filter bar (date range + account) with a live total.
- Share-of-spending doughnut + a sortable top-categories table
  (total, transaction count, average, share bar + percentage).
- **Click any category to drill down**: summary tiles, a per-category 6-month
  trend bar chart, and the full filtered transaction list for that category.

### Action Needed
A single page that surfaces everything requiring attention, in three tabs:
- **Uncategorized** — transactions with no category; assign one inline.
- **Uncertain** — transactions the converter couldn't confidently categorize
  (`category_event == "uncertain"`); review and assign.
- **Skipped rows** — rows the importer could not parse (e.g. unquoted commas),
  with source file, line number, reason, and the raw line so you can fix the CSV.
- Inline category edits save to the DB **and** write back to the source CSV.
- "Save all edits to CSV" syncs every statement file from current DB state.

### Categories & Accounts
- Category tree (parent/child) with per-category colors.
- Account management with **account types**: `checking`, `savings`,
  `credit_card`, `investment` — the type drives how transactions count in stats.

### Rules
- User-defined categorization rules (match on description / account / amount,
  substring or regex, with priority).
- "Apply to all transactions" — rules run only on rows the converter left
  uncategorized, so the intelligent guess always takes precedence.

### Intelligent categorization (baked into statement → CSV)
The MCP `convert_statement` tool runs a built-in 3-layer classifier while
emitting each canonical row, filling `category` + `category_event`:
1. **Merchant / description patterns** — curated regex list (Coffee, Dining,
   Groceries, Transport, Travel, Utilities, Income, Fees, …).
2. **HK calendar events** — date-window rules for `lunar_new_year`,
   `christmas`, `halloween`, `mid_autumn`, `easter`, `payday`, `school_holiday`
   (CNY / Mid-Autumn / Easter windows hard-coded through 2028).
3. **Amount heuristics** — round positive amounts ≥ 1000 with a
   salary/deposit keyword → Income; refunds → Refund.
On import the category text is mapped to a Category row (case-insensitive,
auto-created if missing). No separate categorization step is needed.

### Transfer detection
When you pay a credit card or fund a brokerage from a bank account, both legs
are emitted and automatically paired (opposite-signed, equal-magnitude within
±3 days, different accounts, transfer keywords). Paired transfers are excluded
from spending/income and the category donut, but kept in `total_balance`.

### Investment account handling
Transactions on `investment`-type accounts are classified by description into
`buy` / `sell` / `dividend` / `interest` / `fee`. Buys and sells are treated as
asset swaps (excluded from spending/income); dividends and interest count as
income; fees count as spending. Cash funding rows pair as transfers.

### CSV save-back
Edits made in the UI (category, description, transfer flag) can be written back
to the source `statements/*.csv` file so the on-disk file stays in sync with the
DB. A `.bak` backup is created per file. Rows with no matching transaction
(skipped/malformed) are preserved verbatim.

## Quick start

```bash
# 1. Create a venv and install
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux
pip install -e ".[dev]"

# 2. Configure (optional — defaults work out of the box)
copy .env.example .env            # Windows
# cp .env.example .env            # macOS/Linux

# 3. Run the app
finance-assistance                # starts uvicorn on 127.0.0.1:8000
# or: python -m app.main
```

Open <http://127.0.0.1:8000>.

## Using it

1. **Drop statements** into `statements/` (CSV or TXT).
   - For PDFs or unusual layouts, ask the in-IDE agent to run the
     `convert_statement` MCP tool (see below) to produce a canonical CSV
     first, then drop that CSV in `statements/`.
2. Open the **Import** page and click **Import all**. Duplicates are skipped
   automatically (deduped by `sha256(date|description|amount|account)`).
   Skipped rows are recorded and shown on the **Action Needed** page.
3. Review the **Action Needed** page for uncategorized / uncertain / skipped
   rows; assign categories inline (edits save back to the source CSV).
4. Explore the **Spending** page to drill into spending by category with
   per-category trends and transaction lists.
5. Add **Rules** (e.g. "description contains `STARBUCKS` → Coffee") and click
   *Apply to all transactions* to auto-categorize any remaining rows.
6. The **Dashboard** shows balance, this-month spending/income/net, a category
   donut, a 6-month trend, and an investments summary.

## Canonical CSV schema

Every normalized statement (built-in parser or MCP output) conforms to:

```
date,description,amount,currency,account,balance_after,original_row,category,category_event
```

- `date` — `YYYY-MM-DD`
- `amount` — signed float; **negative = debit, positive = credit**
- `currency` — ISO 4217 (default `HKD`)
- `account`, `balance_after`, `original_row` — optional
- `category` — the converter's best-guess category (free text matching the app
  taxonomy); `""` if uncertain. The importer maps it to a Category row.
- `category_event` — what drove the guess: `merchant_pattern`,
  `lunar_new_year`, `christmas`, `halloween`, `mid_autumn`, `easter`, `payday`,
  `school_holiday`, `amount_heuristic`, or `uncertain`.

> `is_transfer` and `transfer_pair_id` are set by the app after import, not by
> the converter.

Full rules, the categorization layers, and per-bank quirks:
[`mcp_server/INSTRUCTIONS.md`](mcp_server/INSTRUCTIONS.md).

## Statement → CSV via MCP

A local MCP server is registered in [`.cursor/mcp.json`](.cursor/mcp.json) and
exposes:

- `convert_statement(input_path, output_path, format_hint=None)` — convert any
  supported statement (CSV/TXT, or PDF if `pip install pypdf`) into a canonical
  CSV written to `output_path`. **Categories are guessed during conversion.**
- `canonical_schema()` — returns the 9-field schema as JSON.

The agent reads `mcp_server/INSTRUCTIONS.md` for the conversion contract
(including the merchant-pattern + HK calendar-event intelligence), then calls
the tool. Run the server manually for testing:

```bash
python -m mcp_server.server
```

## Adding a new bank (built-in parser)

The app is designed to be expandable. To add a known bank format that doesn't
need the MCP round-trip, register a parser in
[`app/services/parser.py`](app/services/parser.py):

```python
@register("mybank")
def parse_mybank(reader, path):
    for row in reader:
        yield {
            "date": row.get("Txn Date"),
            "description": row.get("Memo"),
            "amount": row.get("Amount"),
            "account": "MyBank Checking",
        }
parse_mybank._detect_headers = {"txn date", "memo", "amount"}
```

Files named `mybank_*.csv` will use it automatically; otherwise the importer
falls back to header-detection heuristics and then the generic parser.

## Switching to PostgreSQL

The app uses SQLAlchemy with generic column types only. Set `DATABASE_URL`:

```
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/finance
```

Then `pip install psycopg[binary]` and restart. Tables are created
automatically on startup.

## Project layout

```
app/                 FastAPI app (config, models, services, routes, templates)
  services/          normalizer, parser, importer, category_service, rule_engine,
                     transfer_service, investment_service, csv_sync
mcp_server/          MCP server, converters, category_classifier, INSTRUCTIONS.md
statements/          drop raw statements here (watched folder)
data/                SQLite database (gitignored)
tests/               pytest suite (normalizer / parser / importer / classifier / transfers / investments)
.cursor/mcp.json     project-scoped MCP server registration
```

## Tests

```bash
pytest
```

## Roadmap / out of scope (current phase)

- Auth / multi-user
- YNAB-style envelope budgeting (Mint-style overview only for now)
- Background folder watcher (import is triggered from UI/API today)
- Reliable PDF table extraction (text-layer only; use MCP + manual review)
