"""MCP server exposing a `convert_statement` tool.

Reads `mcp_server/INSTRUCTIONS.md` for the canonical schema and conversion rules.
Run via the `finance-mcp` console script or `python -m mcp_server.server`.

Registered for Cursor in `.cursor/mcp.json`.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# Make the app package importable when this server is launched directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.normalizer import canonical_csv_string  # noqa: E402
from mcp_server.converters import to_canonical_csv  # noqa: E402
from mcp_server import converters as converters_mod  # noqa: E402

mcp = FastMCP("finance-assistance")


@mcp.tool()
def convert_statement(
    input_path: str,
    output_path: str,
    format_hint: Optional[str] = None,
) -> str:
    """Convert a bank/credit-card statement into the canonical CSV schema.

    Args:
        input_path: Absolute path to the source statement (CSV, TXT, or PDF
            if `pypdf` is installed).
        output_path: Absolute path where the canonical CSV will be written.
        format_hint: Optional bank/format name (e.g. "chase"). Currently the
            converter uses generic heuristics; hints are accepted for
            forward compatibility.

    Returns:
        JSON string with: {rows, account_guess, currency_guess, warnings, output_path}.
    """
    try:
        src = Path(input_path).expanduser().resolve()
        dst = Path(output_path).expanduser().resolve()
        if not src.exists():
            return json.dumps({"error": f"input not found: {src}"})
        dst.parent.mkdir(parents=True, exist_ok=True)

        from app.config import get_settings
        result = converters_mod.convert(src, default_currency=get_settings().default_currency)
        csv_text = to_canonical_csv(result)
        dst.write_text(csv_text, encoding="utf-8")

        return json.dumps({
            "rows": result.rows,
            "row_count": len(result.rows),
            "account_guess": result.account_guess,
            "currency_guess": result.currency_guess,
            "warnings": result.warnings,
            "output_path": str(dst),
        }, indent=2)
    except Exception as e:  # pragma: no cover - surfaced to the agent
        return json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
        })


@mcp.tool()
def canonical_schema() -> str:
    """Return the canonical CSV schema as JSON (fields + rules summary)."""
    return json.dumps({
        "fields": [
            {"name": "date", "type": "date", "format": "YYYY-MM-DD", "required": True},
            {"name": "description", "type": "string", "required": True},
            {"name": "amount", "type": "float", "sign": "negative=debit, positive=credit", "required": True},
            {"name": "currency", "type": "string", "format": "ISO 4217", "required": True},
            {"name": "account", "type": "string", "required": False},
            {"name": "balance_after", "type": "float", "required": False},
            {"name": "original_row", "type": "string", "required": False, "note": "raw source for audit"},
            {"name": "category", "type": "string", "required": False,
             "note": "converter's best-guess category (free text matching the app taxonomy). Baked into the statement->CSV step via merchant patterns + HK calendar events. '' if uncertain."},
            {"name": "category_event", "type": "string", "required": False,
             "note": "event that drove the guess: 'merchant_pattern', 'lunar_new_year', 'christmas', 'halloween', 'mid_autumn', 'easter', 'payday', 'school_holiday', 'amount_heuristic', or 'uncertain'."},
        ],
        "sign_convention": "amount is signed: debits negative, credits positive",
        "categorization": "The converter fills `category`+`category_event` automatically. The app maps the text to a Category row on import. User rules run only on rows the converter left uncategorized.",
        "see": "mcp_server/INSTRUCTIONS.md",
    }, indent=2)


def run() -> None:
    """Entry point for the `finance-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    run()
