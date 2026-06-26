# MCP Instructions: Statement → Canonical CSV

This file is the contract the agent follows when converting **any** bank or
credit-card statement into the canonical CSV format the Finance Assistance app
expects. Read it before calling the `convert_statement` MCP tool.

The companion MCP server (`mcp_server/server.py`) exposes:

- `convert_statement(input_path, output_path, format_hint=None)` — convert a file
- `canonical_schema()` — machine-readable schema summary

> **When to use which path**
> - File already looks like clean CSV with recognizable columns → drop it in
>   `statements/` and let the app's built-in parser handle it. No MCP needed.
> - File is a PDF, an unusual layout, or has columns the app doesn't recognize →
>   call `convert_statement` to produce a canonical CSV in `statements/`, then
>   import from the UI.

---

## 1. Canonical CSV schema

Every converted statement must contain exactly these columns, in this order:

| # | field            | type   | required | notes                                                        |
|---|------------------|--------|----------|--------------------------------------------------------------|
| 1 | `date`           | date   | yes      | ISO `YYYY-MM-DD`                                             |
| 2 | `description`    | string | yes      | free text; merchant/memo                                     |
| 3 | `amount`         | float  | yes      | **signed**: negative = debit (money out), positive = credit |
| 4 | `currency`       | string | yes      | ISO 4217 (`HKD`, `USD`, `CNY`, …)                            |
| 5 | `account`        | string | no       | account name/number from the statement                       |
| 6 | `balance_after`  | float  | no       | running balance after the transaction, if known              |
| 7 | `original_row`   | string | no       | the raw source line, for audit/debug                         |
| 8 | `category`       | string | no       | converter's best-guess category (free text matching the app's taxonomy). Baked into the statement→CSV step — see "Categorization" below. `""` if uncertain. |
| 9 | `category_event` | string | no       | event that drove the guess: `merchant_pattern`, `lunar_new_year`, `christmas`, `halloween`, `mid_autumn`, `easter`, `payday`, `school_holiday`, `amount_heuristic`, or `uncertain`. |

> **Note:** `is_transfer` and `transfer_pair_id` are set by the app after import,
> not by the converter. The converter emits the 9 fields above; the app's
> transfer detector pairs matching rows (see "Transfers" below) and the
> importer maps the `category` text to a Category row.

Example canonical CSV:

```csv
date,description,amount,currency,account,balance_after,original_row,category,category_event
2024-05-01,STARBUCKS STORE #123,-4.50,HKD,Checking,995.50,"Date=2024-05-01 | Description=STARBUCKS #123 | Amount=-4.50 | Balance=995.50",Coffee,merchant_pattern
2024-05-02,ACME CORP PAYROLL,2000.00,HKD,Checking,2995.50,"Date=2024-05-02 | Description=ACME CORP PAYROLL | Amount=2000.00 | Balance=2995.50",Income,merchant_pattern
```

---

## 2. Categorization (built into the statement→CSV step)

The converter does **not** leave rows uncategorized for a separate step. While
emitting each canonical row it runs a built-in classifier
(`mcp_server/category_classifier.py`) that fills `category` + `category_event`
using three layers, in priority order:

### Layer 1 — Merchant / description patterns (`category_event = "merchant_pattern"`)
The description is matched case-insensitively against a curated pattern list.
First match wins. Examples:

| Pattern keywords (regex, case-insensitive)                              | `category`        |
|-------------------------------------------------------------------------|-------------------|
| `starbucks`, `pacific coffee`, `% Arabica`, `costa coffee`              | Coffee            |
| `mcdonald`, `kfc`, `pizza hut`, `café de coral`, `fairwood`             | Fast Food         |
| `restaurant`, `sushi`, `ramen`, `cha chaan teng`, `dim sum`, `hotpot`   | Dining            |
| `wellcome`, `parknshop`, `fusion`, `7-eleven`, `circle k`               | Groceries         |
| `deliveroo`, `foodpanda`, `uber eats`                                   | Dining            |
| `mtr`, `octopus`, `kmb`, `citybus`, `taxi`, `uber`, `hk tram`           | Transport         |
| `shell`, `cnooc`, `sinopec`, `fuel`, `petrol`                           | Fuel              |
| `cathay`, `hk express`, `trip.com`, `klook`, `agoda`, `flight`, `hotel` | Travel            |
| `h&m`, `uniqlo`, `zara`, `muji`, `m&s`                                  | Clothing          |
| `amazon`, `taobao`, `shopee`, `hktv mall`                               | Online Shopping   |
| `apple`, `samsung`, `fortress`, `broadway`, `electronics`               | Electronics       |
| `watsons`, `mannings`, `sasa`, `sephora`                                | Health & Beauty   |
| `clp`, `hk electric`, `towngas`, `water supplies`                       | Utilities         |
| `pccw`, `now tv`, `smartone`, `csl`, `3hk`, `cmhk`, `broadband`         | Phone & Internet  |
| `netflix`, `spotify`, `disney+`, `apple music`, `youtube premium`       | Subscriptions     |
| `salary`, `payroll`, `wages`, `bonus`                                   | Income            |
| `mpf`, `mandatory provident`, `pension`                                 | Pension           |
| `tax`, `ird`, `inland revenue`                                          | Tax               |
| `aia`, `prudential`, `manulife`, `axa`, `insurance`                     | Insurance         |
| `interest`                                                              | Interest          |
| `fee`, `charge`, `annual fee`, `service charge`                         | Fees              |
| `atm`, `cash withdrawal`                                                | Cash              |
| `transfer`, `wire`, `fps`, `ach`, `deposit from`                        | Transfer          |
| `tuition`, `school`, `university`, `course`, `coursera`                 | Education         |
| `donation`, `charity`, `red cross`, `oxfam`, `unicef`                   | Charity           |

The full, always-current list lives in `MERCHANT_PATTERNS` in
`category_classifier.py`. Extend it there — no separate config file.

### Layer 2 — HK calendar events (`category_event = "lunar_new_year"` etc.)
If no merchant pattern matched, the converter looks at the transaction
**date** plus a keyword in the description. The event fires only when the date
falls in the event window **and** the description contains the keyword:

| `category_event` | date window (HK)                                  | keyword(s)                                | `category` |
|------------------|---------------------------------------------------|-------------------------------------------|------------|
| `lunar_new_year` | CNY ±2 days (2024-02-09..13, 2025-01-28..02-01, …) | `lai see`, `red packet`, `cny`, `reunion` | Gifts      |
| `lunar_new_year` | same                                              | `dinner`, `feast`, `family meal`          | Dining     |
| `christmas`      | December                                          | `christmas`, `gift`, `present`, `turkey`  | Gifts      |
| `christmas`      | December                                          | `dinner`, `party`, `feast`                | Dining     |
| `halloween`      | Oct 20–31                                         | `costume`, `candy`, `pumpkin`, `party`    | Entertainment |
| `mid_autumn`     | Mid-Autumn ±1 day (2024-09-16..18, 2025-10-05..07, …) | `mooncake`, `lantern`, `family`        | Gifts      |
| `easter`         | Good Friday–Easter Monday (HK public holiday)     | `easter`, `egg`, `bunny`, `church`        | Gifts      |
| `payday`         | 1st / 15th / 25th / last working day of month      | `salary`, `payroll`, `wages`              | Income     |
| `school_holiday` | mid-July – end of August (HK summer break)         | `travel`, `flight`, `hotel`, `vacation`   | Travel     |

CNY / Mid-Autumn / Easter windows shift each year because they follow the
lunar / ecclesiastical calendar; the table hard-codes windows through 2028 —
add new years in `_is_lunar_new_year` / `_is_mid_autumn` / `_is_easter`.

### Layer 3 — Amount heuristics (`category_event = "amount_heuristic"`)
- Round positive amounts ≥ 1000 with a `salary`/`payroll`/`bonus`/`deposit`
  keyword → `Income`.
- Positive amounts with `refund`/`rebate`/`cashback` → `Refund`.

### Fallback
If none of the three layers match, the converter emits
`category=""` and `category_event="uncertain"`. On import the app leaves the
row Uncategorized and the user's rule engine then gets a chance to categorize
it. **User rules never overwrite a converter-supplied category** — the
intelligent guess takes precedence.

### How the app consumes the guess
The importer (`app/services/importer.py`) maps the `category` text to a
`Category` row by case-insensitive name match. If no category with that name
exists yet, it **creates one** (grey color, top-level) so the guess is never
lost. The `category_event` text is stored on the `Transaction` row and shown
as a small badge in the Transactions page for transparency.

---

## 3. Conversion rules

### Date
- Always emit `YYYY-MM-DD`.
- **Hong Kong convention: day-first.** Interpret `DD/MM/YYYY` (and `DD-MM-YYYY`)
  as day-month-year. ISO `YYYY-MM-DD` is handled unambiguously.
- Accept `MM/DD/YYYY` only when unambiguous (day > 12). `Mon DD, YYYY` is fine.
- If a statement has both "transaction date" and "posting date", prefer the
  **transaction date**.

### Amount sign
- Money **leaving** the account (debit, charge, withdrawal, "paid out") → **negative**.
- Money **entering** the account (credit, deposit, refund, "paid in") → **positive**.
- Parentheses `(12.34)` → negative.
- Trailing `DR`/`DB`/`-` → negative. Trailing `CR`/`+` → positive.
- Some statements split into two columns (`Debit`, `Credit`). Combine:
  `amount = credit - debit`.
- Strip currency symbols, thousand separators. Handle European `1.234,56`.

### Currency
- If a `Currency`/`CCY` column exists, use it (uppercased).
- Otherwise default to **HKD** (Hong Kong Dollar). Prefer explicit data from the
  statement when present.
- Common HK account currencies: `HKD`, `USD`, `CNY`, `GBP`, `EUR`.

### Account
- Copy from an `Account`/`Card`/`Account Number` column if present.
- Otherwise use the file stem (e.g. `chase_may.csv` → `chase_may`).

### Balance
- Include `balance_after` only if the statement provides a running balance.
- Leave empty when unknown — do **not** fabricate.

### Multi-row transactions
- Keep each posted transaction on its own row. Pending/authorization-only rows
  should be skipped (or flagged via a warning) to avoid double counting once
  they post.

### Deduplication
- The app dedupes by `sha256(date | description | amount | account)` (case- and
  whitespace-insensitive). Re-running conversion on the same file is safe.

### Transfers (bank ↔ credit-card payments)
When you pay a credit card from a bank account, **two rows** are expected and
correct — do **not** suppress either side:

  - bank statement row:  `description ≈ "PAYMENT TO <CARD>"`,  `amount = -X` (negative)
  - card statement row:  `description ≈ "PAYMENT RECEIVED"`,   `amount = +X` (positive)

Rules for these rows:
- Emit both with their real signed amounts and their respective `account`
  values (the bank account on the negative row, the card on the positive row).
- Keep `account` different on the two rows — the app pairs them by
  **opposite-signed amount with equal magnitude (±1.00), within ±3 days, on
  different accounts**, and at least one description matching a transfer
  keyword (`credit card`, `card payment`, `payment received`, `payment to/from`,
  `transfer to/from`, `thank you`, `visa`, `mastercard`).
- Currency must match across the pair (cross-currency transfers are not
  auto-paired; flag those for manual review).

Once paired, the app sets `is_transfer=True` on both rows and links them via
`transfer_pair_id`. Paired transfers are **excluded from spending, income, and
the spending-by-category donut**, but **kept in `total_balance`** (moving money
between your own accounts doesn't change net worth) and shown in the
transactions list (with a Transfer checkbox and a muted row style). The user can
also toggle the Transfer flag manually from the transactions page, or trigger
detection from the Import page (`POST /api/transfers/detect`).

Worked example (bank → HSBC credit card, HKD):

```csv
date,description,amount,currency,account,balance_after,original_row,category,category_event
2024-05-10,PAYMENT TO HSBC CREDIT CARD,-5000.00,HKD,HSBC Checking,15000.00,...,Transfer,merchant_pattern
2024-05-12,PAYMENT RECEIVED - THANK YOU,5000.00,HKD,HSBC Credit Card,0.00,...,Transfer,merchant_pattern
```

→ the importer pairs these two rows automatically.

### Investment accounts (brokerage)

Statements from investment accounts (brokerage) are converted with the **same
canonical schema** — no extra columns. The app treats them specially based on
the **account type**, which the user sets on the Accounts page (one of
`checking`, `savings`, `credit_card`, `investment`). Conversion guidance:

1. **Emit every row as-is** (date, description, signed amount, account, etc.).
   Do not collapse buys/sells or strip ticker symbols — the description text is
   used to classify the row.
2. **Description wording matters.** The app classifies each row on an
   investment account by matching keywords in `description`:

   | `kind`     | matched keywords (case-insensitive, word-boundary)        | stats treatment                              |
   |------------|-----------------------------------------------------------|----------------------------------------------|
   | `buy`      | `buy`, `bought`, `purchase`, `purch`, `acquisition`, `acq` | asset swap — **excluded** from spending      |
   | `sell`     | `sell`, `sold`, `sale`, `dispose`, `disposal`            | asset swap — **excluded** from income        |
   | `dividend` | `dividend`, `dvd`, `div rec`, `distribution`, `dist rec` | **counted as income**                        |
   | `interest` | `interest`, `sweep interest`, `cash interest`, `int rec` | **counted as income**                        |
   | `fee`      | `commission`, `comm`, `platform fee`, `management fee`, `custody fee`, `fee`, `transaction charge`, `service charge` | **counted as spending** |
   | `""`       | (none of the above)                                       | generic cash movement → may pair as transfer |

3. **Cash deposits / withdrawals** between the bank and the brokerage
   (`WIRE OUT TO BROKERAGE`, `ACH DEPOSIT FROM BANK`, `FUNDING`, etc.) are
   **transfers** — emit both legs (negative on the bank, positive on the
   brokerage, same magnitude, within ±3 days). The transfer detector pairs them
   automatically. Rows already classified as buy/sell/dividend/interest/fee are
   **never** paired as transfers (so a `SELL AAPL +1500` won't be mis-paired
   with an unrelated `-1500` debit).
4. **`total_balance` includes everything** (cash + securities value proxied by
   net amount), so buying a stock doesn't change net worth. Spending/income
   exclude buys & sells but keep dividends, interest, and fees.

Worked example (IBKR statement, `account=IBKR`, account type=`investment`):

```csv
date,description,amount,currency,account,balance_after,original_row,category,category_event
2024-05-01,BUY AAPL 10 SH @ 150,-1500.00,HKD,IBKR,8000.00,...,,uncertain
2024-05-05,SELL AAPL 5 SH @ 160,800.00,HKD,IBKR,8800.00,...,,uncertain
2024-05-10,DIVIDEND AAPL Q2,12.50,HKD,IBKR,8812.50,...,Interest,merchant_pattern
2024-05-12,PLATFORM FEE,-5.00,HKD,IBKR,8807.50,...,Fees,merchant_pattern
2024-05-15,ACH DEPOSIT FROM BANK,5000.00,HKD,IBKR,13807.50,...,Transfer,merchant_pattern
```

→ app tags the rows `buy`, `sell`, `dividend`, `fee`, `` (transfer-leg).
The `-1500` buy is **not** spending; the `+800` sell is **not** income; the
`+12.50` dividend **is** income; the `-5.00` fee **is** spending; the
`+5000` deposit pairs with the bank's `-5000 WIRE OUT TO BROKERAGE` as a transfer.

---

## 4. Per-bank quirks (extend this table as new banks are added)

| Bank / format        | Quirks                                                                                       |
|----------------------|----------------------------------------------------------------------------------------------|
| HSBC HK              | `Date`, `Description`, `Paid out`, `Paid in`, `Balance`. Date `DD/MM/YYYY`. HKD. Combine: `amount = paid_in - paid_out`. |
| Hang Seng            | Same layout as HSBC HK; CSV export uses `DD/MM/YYYY`. HKD.                                  |
| IBKR / brokerage     | Investment account — set account type to `investment`. Emit every row; app classifies buy/sell/dividend/interest/fee from the description. Cash funding rows pair as transfers. |
| Chase (US credit)    | Columns: `Transaction Date`, `Description`, `Amount`, `Balance`. Amount already signed. USD.|
| Amex                 | Columns: `Date`, `Description`, `Amount`. Amount already signed (positive = charge).        |
| Amazon (orders CSV)  | `Date`, `Description`, `Amount`, `Balance`. Treat as a generic account named "Amazon".       |
| UK bank              | `Date`, `Description`, `Paid out`, `Paid in`. Combine: `amount = paid_in - paid_out`. GBP. Date `DD/MM/YYYY`.   |
| European (SEPA)      | Decimal comma `12,34`. Date `DD.MM.YYYY`. Currency EUR.                                      |
| PDF (text-layer)     | Run `convert_statement` with `pypdf` installed; review warnings — tables may need manual fix.|

To add a new bank: append a row here and, if its format is common, add a
`@register("bank_name")` parser in `app/services/parser.py`.

---

## 5. How to invoke the tool

```
convert_statement(
  input_path="/abs/path/to/statement.pdf",
  output_path="/abs/path/to/statements/statement_canonical.csv",
  format_hint="chase"   # optional
)
```

The tool returns JSON:

```json
{
  "row_count": 42,
  "account_guess": "chase_may",
  "currency_guess": "USD",
  "warnings": ["skipped row lacking date/amount: ..."],
  "output_path": "/abs/path/to/statements/statement_canonical.csv"
}
```

After conversion, the file is in `statements/` and the user imports it from the
**Import** page (or `POST /api/import`).

---

## 6. Worked example

**Input** (`hsbc_hk.csv`):

```csv
Date,Description,Paid out,Paid in,Balance
01/05/2024,PRET A MANGER,4.50,,995.50
02/05/2024,SALARY,,2000.00,2995.50
```

**Expected canonical output** (HK day-first dates, HKD):

```csv
date,description,amount,currency,account,balance_after,original_row,category,category_event
2024-05-01,PRET A MANGER,-4.50,HKD,hsbc_hk,995.50,...,Dining,merchant_pattern
2024-05-02,SALARY,2000.00,HKD,hsbc_hk,2995.50,...,Income,merchant_pattern
```

Dates are interpreted day-first per the Hong Kong convention:
`01/05/2024` → 1 May 2024, `02/05/2024` → 2 May 2024.
