"""Heuristic category classifier used by the statement->CSV converter.

This is the "intelligent" category guess baked into the conversion step
(see `mcp_server/INSTRUCTIONS.md`). It combines:

1. Merchant / description patterns (highest priority).
2. HK calendar events (date-driven): lunar new year, christmas, mid-autumn,
   halloween, payday, school holidays, etc.
3. Amount heuristics (round numbers, recurring amounts).

It is deliberately conservative: when nothing matches, it returns
("", "uncertain") and the row is left uncategorized for the user / rule engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class CategoryGuess:
    category: str  # taxonomy name, "" if unknown
    event: str  # what drove the guess; "uncertain" if nothing matched


# --- Merchant / description patterns -------------------------------------
# Order matters: first match wins. Patterns are matched case-insensitively
# against the full description. Each entry is (regex, category, event_label).
MERCHANT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # --- Food & drink ---
    (re.compile(r"\b(starbucks|pacific coffee|％ Arabica|arabica|costa coffee)\b", re.I), "Coffee", "merchant_pattern"),
    (re.compile(r"\b(mcdonald|kfc|burger king|subway|pizza hut|domino|five guys|shake shack|caf[eé] de coral|fairwood|cafe de coral)\b", re.I), "Fast Food", "merchant_pattern"),
    (re.compile(r"\b(restaurant|sushi|ramen|izakaya|bistro|diner|noodle|cha chaan teng|tea restaurant|dim sum|hotpot|hot pot)\b", re.I), "Dining", "merchant_pattern"),
    (re.compile(r"\b(food[\s-]?market|wellcome|parknshop|pns|fusion|market place|759|7-?eleven|circle k|ok mart|grocery|supermarket)\b", re.I), "Groceries", "merchant_pattern"),
    (re.compile(r"\b(deliveroo|foodpanda|uber\s?eats|food delivery)\b", re.I), "Dining", "merchant_pattern"),
    (re.compile(r"\b(bakery|cake|pastry|bread)\b", re.I), "Bakery", "merchant_pattern"),
    # --- Transport ---
    (re.compile(r"\b(mtr|octopus|kmb|citybus|long win|minibus|taxi|uber|lyft|hktaxi|crown motors|mps|hk tram)\b", re.I), "Transport", "merchant_pattern"),
    (re.compile(r"\b(shell|cnooc|sinopec|esso|mobil|fuel|petrol|gas station)\b", re.I), "Fuel", "merchant_pattern"),
    (re.compile(r"\b(cathay|hk express|hong kong airlines|airline|flight|cxl|trip\.com|expedia|booking\.com|klook|agoda)\b", re.I), "Travel", "merchant_pattern"),
    (re.compile(r"\b(hotel|hostel|marriott|hyatt|hilton|shangri|mandarin)\b", re.I), "Travel", "merchant_pattern"),
    # --- Shopping ---
    (re.compile(r"\b(h[\.\s]?m\b|uniqlo|zara|gap|muji|marks? & spencer|m&s|hennes|clothing|apparel|fashion)\b", re.I), "Clothing", "merchant_pattern"),
    (re.compile(r"\b(amazon|taobao|shopee|aliexpress|ebay|temu|hktv ?mall)\b", re.I), "Online Shopping", "merchant_pattern"),
    (re.compile(r"\b(apple|samsung|huawei|xiaomi|best buy|fortress|broadway|electronics|gadget)\b", re.I), "Electronics", "merchant_pattern"),
    (re.compile(r"\b(watsons|mannings|cosme|sasa|drugstore|pharmacy|cosmetics|sephora)\b", re.I), "Health & Beauty", "merchant_pattern"),
    (re.compile(r"\b(ikea|home|furniture|deco[r]?|bedding|lifestyle)\b", re.I), "Home", "merchant_pattern"),
    # --- Bills & utilities ---
    (re.compile(r"\b(clp|hk electric|china light|power|electricity|water supplies|wsd|gas|towngas|heng qin gas)\b", re.I), "Utilities", "merchant_pattern"),
    (re.compile(r"\b(pccw|now tv|netvigator|hk broadband|smartone|csl|3hk|china mobile|china mobile hong kong|cmhk|vodafone|mobile|broadband|internet)\b", re.I), "Phone & Internet", "merchant_pattern"),
    (re.compile(r"\b(management fee|rates|government rent|estate|housing society|hkus|housing)\b", re.I), "Housing", "merchant_pattern"),
    # --- Health ---
    (re.compile(r"\b(clinic|hospital|doctor|medical|dentist|pharmacy|health|physio)\b", re.I), "Healthcare", "merchant_pattern"),
    (re.compile(r"\b(gym|fitness|pure yoga|cali|fitnation|yoga|sports|classpass)\b", re.I), "Fitness", "merchant_pattern"),
    # --- Entertainment ---
    (re.compile(r"\b(netflix|spotify|disney|hbo|hulu|youtube premium|apple (music|tv)|streaming)\b", re.I), "Subscriptions", "merchant_pattern"),
    (re.compile(r"\b(cinema|movie|ticket|macc|uat|broadway circuit|imax)\b", re.I), "Entertainment", "merchant_pattern"),
    (re.compile(r"\b(steam|playstation|psn|xbox|nintendo|game|steamgames)\b", re.I), "Gaming", "merchant_pattern"),
    # --- Financial ---
    (re.compile(r"\b(salary|payroll|wages|pay ?check|bonus|commission|wages)\b", re.I), "Income", "merchant_pattern"),
    (re.compile(r"\b(mp ?f|mandatory provident|pension|retirement)\b", re.I), "Pension", "merchant_pattern"),
    (re.compile(r"\b(tax|ird|inland revenue)\b", re.I), "Tax", "merchant_pattern"),
    (re.compile(r"\b(insurance|aia|prudential|manulife|axa|hsbc life)\b", re.I), "Insurance", "merchant_pattern"),
    (re.compile(r"\b(interest|interest earned|interest paid)\b", re.I), "Interest", "merchant_pattern"),
    (re.compile(r"\b(fee|charge|service charge|annual fee|late fee|overdraft fee)\b", re.I), "Fees", "merchant_pattern"),
    (re.compile(r"\b(atm|cash withdrawal|cash deposit)\b", re.I), "Cash", "merchant_pattern"),
    (re.compile(r"\b(transfer|wire|tt|telegraphic|fps|faster payment|ach|deposit from|withdrawal to)\b", re.I), "Transfer", "merchant_pattern"),
    # --- Education ---
    (re.compile(r"\b(tuition|school|university|course|edx|coursera|udemy|training|exam)\b", re.I), "Education", "merchant_pattern"),
    (re.compile(r"\b(book|bookstore|dymocks|commercial press)\b", re.I), "Books", "merchant_pattern"),
    # --- Charity ---
    (re.compile(r"\b(donation|charity|red cross|oxfam|unicef|salvation army)\b", re.I), "Charity", "merchant_pattern"),
]


# --- HK calendar events --------------------------------------------------
# Each entry: (regex keyword, category override, event_label, date window).
# date_window is a callable(date) -> bool; if the transaction date falls in the
# window AND the description matches the keyword, the event fires.

def _is_lunar_new_year(d: date) -> bool:
    # Approximate windows for HK CNY public holidays (lunar calendar varies
    # by ~1 day; widen to a 7-day window around the known dates).
    cny_windows = [
        (date(2024, 2, 9), date(2024, 2, 13)),   # Year of Dragon
        (date(2025, 1, 28), date(2025, 2, 1)),   # Year of Snake
        (date(2026, 2, 16), date(2026, 2, 20)),  # Year of Horse
        (date(2027, 2, 5), date(2027, 2, 9)),    # Year of Goat
        (date(2028, 1, 25), date(2028, 1, 29)),  # Year of Rooster
    ]
    return any(s <= d <= e for s, e in cny_windows)


def _is_christmas(d: date) -> bool:
    # HK treats Dec 25-27 as public holidays; shopping peaks Dec 1 - Jan 1.
    return d.month == 12 and d.day >= 1


def _is_halloween(d: date) -> bool:
    return d.month == 10 and 20 <= d.day <= 31


def _is_mid_autumn(d: date) -> bool:
    # Approximate Mid-Autumn dates.
    windows = [
        (date(2024, 9, 16), date(2024, 9, 18)),
        (date(2025, 10, 5), date(2025, 10, 7)),
        (date(2026, 9, 24), date(2026, 9, 26)),
        (date(2027, 9, 14), date(2027, 9, 16)),
        (date(2028, 10, 2), date(2028, 10, 4)),
    ]
    return any(s <= d <= e for s, e in windows)


def _is_easter(d: date) -> bool:
    # HK Easter public holiday: Good Friday + Easter Monday.
    windows = [
        (date(2024, 3, 29), date(2024, 4, 1)),
        (date(2025, 4, 18), date(2025, 4, 21)),
        (date(2026, 4, 3), date(2026, 4, 6)),
        (date(2027, 3, 26), date(2027, 3, 29)),
        (date(2028, 4, 14), date(2028, 4, 17)),
    ]
    return any(s <= d <= e for s, e in windows)


def _is_payday(d: date) -> bool:
    # HK typical payday: last working day of month, or 1st/15th/25th.
    return d.day in (1, 15, 25) or d.day >= 28


def _is_school_holiday(d: date) -> bool:
    # HK school summer holidays: mid-July to end of August.
    return (d.month == 7 and d.day >= 10) or d.month == 8


EVENT_RULES: list[tuple[re.Pattern, str, str, callable]] = [
    # (keyword, category, event_label, date_window_fn)
    (re.compile(r"\b(lai see|lai si|red packet|red envelope|fai hinp|cny|chinese new year|lunar new year)\b", re.I), "Gifts", "lunar_new_year", _is_lunar_new_year),
    (re.compile(r"\b(dinner|feast|family meal|reunion|new year)\b", re.I), "Dining", "lunar_new_year", _is_lunar_new_year),
    (re.compile(r"\b(gift|present|shopping|decor|cny|festive)\b", re.I), "Gifts", "lunar_new_year", _is_lunar_new_year),
    (re.compile(r"\b(christmas|xmas|gift|present|tree|turkey|secret santa)\b", re.I), "Gifts", "christmas", _is_christmas),
    (re.compile(r"\b(dinner|party|feast)\b", re.I), "Dining", "christmas", _is_christmas),
    (re.compile(r"\b(costume|candy|pumpkin|party|haunted|lantern)\b", re.I), "Entertainment", "halloween", _is_halloween),
    (re.compile(r"\b(mooncake|moon cake|lantern|family|dinner)\b", re.I), "Gifts", "mid_autumn", _is_mid_autumn),
    (re.compile(r"\b(easter|egg|hot cross|bunny|church)\b", re.I), "Gifts", "easter", _is_easter),
    (re.compile(r"\b(salary|payroll|wages|pay ?check)\b", re.I), "Income", "payday", _is_payday),
    (re.compile(r"\b(travel|flight|airline|hotel|trip|vacation|staycation)\b", re.I), "Travel", "school_holiday", _is_school_holiday),
]


# --- Amount heuristics ---------------------------------------------------
def _amount_heuristic(amount: float, description: str) -> Optional[tuple[str, str]]:
    desc = description.lower()
    # Round positive amounts ending in .00 with a "salary/payroll" hint.
    if amount > 0 and abs(amount - round(amount)) < 0.001 and amount >= 1000:
        if any(w in desc for w in ("salary", "payroll", "wages", "bonus", "deposit")):
            return ("Income", "amount_heuristic")
    # Round positive "refund"-style amounts.
    if amount > 0 and any(w in desc for w in ("refund", "rebate", "cashback", "cash back")):
        return ("Refund", "amount_heuristic")
    return None


def classify(description: str, amount: float, tx_date: Optional[date] = None) -> CategoryGuess:
    """Return a best-guess (category, event) for a transaction.

    Always returns a guess when possible; returns ("", "uncertain") only when
    nothing matches.
    """
    desc = (description or "").strip()
    if not desc:
        return CategoryGuess("", "uncertain")

    # 1) Merchant patterns (highest priority).
    for pattern, category, event in MERCHANT_PATTERNS:
        if pattern.search(desc):
            # Upgrade the event label when this is an Income-pattern match
            # (salary/payroll/wages/bonus) landing on a payday. Keeps the same
            # category; only refines the event for transparency.
            if category == "Income" and tx_date is not None and _is_payday(tx_date):
                event = "payday"
            return CategoryGuess(category, event)

    # 2) HK calendar events (date + keyword).
    if tx_date is not None:
        for pattern, category, event, window_fn in EVENT_RULES:
            if window_fn(tx_date) and pattern.search(desc):
                return CategoryGuess(category, event)

    # 3) Amount heuristics.
    amt_guess = _amount_heuristic(amount, desc)
    if amt_guess is not None:
        return CategoryGuess(*amt_guess)

    return CategoryGuess("", "uncertain")
