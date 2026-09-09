"""Exact 100-contract reference quotes; no network or Discord dependencies.

Fee arithmetic is adapted from kalshi-exec/src/kalshi_exec/fees.py.
This is the agreed no-maker-fee, 0.07-taker model with whole-cent fee rounding,
not a lookup of market-specific or account-specific exchange fees.
"""

from dataclasses import dataclass
from fractions import Fraction
import re

PRICE_SCALE = 10_000  # $1; one unit is 0.01 cent
CONTRACTS = 100
_CENT_PRICE = re.compile(r"([0-9]{1,2})(?:\.([0-9]{1,2}))?[cC]")


def parse_cent_price(token: str) -> int:
    match = _CENT_PRICE.fullmatch(token.strip())
    if match is None:
        raise ValueError("Expected a cent price with at most two decimal places")
    whole, fractional = match.groups()
    price = int(whole) * 100 + int((fractional or "").ljust(2, "0"))
    if not 100 <= price <= 9900:
        raise ValueError("Cent price must be from 1 through 99")
    return price


def taker_fee_cents(contracts: int, price_cc: int) -> int:
    if type(contracts) is not int or contracts <= 0:
        raise ValueError("Contract count must be a positive integer")
    if type(price_cc) is not int or not 0 < price_cc < PRICE_SCALE:
        raise ValueError("Price must be strictly between zero and one dollar")
    numerator = 7 * contracts * price_cc * (PRICE_SCALE - price_cc)
    denominator = PRICE_SCALE * PRICE_SCALE
    return (numerator + denominator - 1) // denominator


@dataclass(frozen=True)
class ContractQuote:
    price_cc: int
    taker_fee_cents: int

    @property
    def contracts(self) -> int:
        return CONTRACTS

    @property
    def notional_cents(self) -> int:
        # At exactly 100 contracts, notional cents equals price in centi-cents.
        return self.price_cc

    @property
    def maker_decimal(self) -> Fraction:
        return Fraction(CONTRACTS * 100, self.notional_cents)

    @property
    def taker_decimal(self) -> Fraction:
        return Fraction(CONTRACTS * 100, self.notional_cents + self.taker_fee_cents)


def quote(price_cc: int) -> ContractQuote:
    if type(price_cc) is not int or not 100 <= price_cc <= 9900:
        raise ValueError("Reference price must be from 1 through 99 cents")
    return ContractQuote(price_cc, taker_fee_cents(CONTRACTS, price_cc))
