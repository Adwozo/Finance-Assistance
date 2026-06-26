# Finance Assistance

A self-hosted personal finance assistant. Drop bank/credit-card statements into
a folder, the app ingests them, categorizes transactions, and renders a
Mint-style HTML dashboard. A local MCP server converts arbitrary statement
formats into a canonical CSV the app understands.

- **Stack:** Python 3.11, FastAPI, Jinja2, SQLAlchemy, TailwindCSS (CDN), Chart.js, HTMX
- **DB:** SQLite by default, PostgreSQL-compatible (no SQLite-specific SQL)
- **UI reference:** Mint / Monarch Money — sidebar accounts/categories, dashboard cards + charts, filterable transactions table

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
3. Review transactions on the **Transactions** page; assign categories inline.
4. Add **Rules** (e.g. "description contains `STARBUCKS` → Coffee") and click
   *Apply to all transactions* to auto-categorize in bulk.
5. The **Dashboard** shows balance, this-month spending/income/net, a category
   donut, and a 6-month trend.

## Canonical CSV schema

Every normalized statement (built-in parser or MCP output) conforms to:

```
date,description,amount,currency,account,balance_after,original_row
```

- `date` — `YYYY-MM-DD`
- `amount` — signed float; **negative = debit, positive = credit**
- `currency` — ISO 4217
- `account`, `balance_after`, `original_row` — optional

Full rules and per-bank quirks: [`mcp_server/INSTRUCTIONS.md`](mcp_server/INSTRUCTIONS.md).

## Statement → CSV via MCP

A local MCP server is registered in [`.cursor/mcp.json`](.cursor/mcp.json) and
exposes:

- `convert_statement(input_path, output_path, format_hint=None)` — convert any
  supported statement (CSV/TXT, or PDF if `pip install pypdf`) into a canonical
  CSV written to `output_path`.
- `canonical_schema()` — returns the schema as JSON.

The agent reads `mcp_server/INSTRUCTIONS.md` for the conversion contract, then
calls the tool. Run the server manually for testing:

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
mcp_server/          MCP server + INSTRUCTIONS.md (statement → canonical CSV)
statements/          drop raw statements here (watched folder)
data/                SQLite database (gitignored)
tests/               pytest suite for normalizer / parser / importer
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
