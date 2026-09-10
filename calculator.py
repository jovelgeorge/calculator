"""Input parsing and all odds, devig, prediction-market, EV, and Kelly math."""

from dataclasses import dataclass
from fractions import Fraction
import math
import re
from statistics import NormalDist


# Prediction-market prices and exact fees (fixed 100-contract reference model).
# Integer fee formulation adapted from kalshi-exec/src/kalshi_exec/fees.py.
PRICE_SCALE = 10_000  # $1; one unit is 0.01 cent
CONTRACTS = 100
_CENT_PRICE = re.compile(r"([0-9]{1,2})(?:\.([0-9]{1,2}))?[cC¢]")


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


# American odds, probit devigging, and complete-message parsing.
MAX_MESSAGE_LENGTH = 2000
MAX_LEGS = 20
_AMERICAN = re.compile(r"[+-]?[0-9]+")
_HOLD = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
_AVERAGE = re.compile(r"avg\s*\(([^()]*)\)", re.IGNORECASE | re.ASCII)
_NORMAL = NormalDist()


def require_probability(probability: Fraction | float) -> None:
    if not 0 < float(probability) < 1:
        raise ValueError("Probability is outside the supported numerical range")


def american_probability(odds: int) -> Fraction:
    if type(odds) is not int or abs(odds) < 100:
        raise ValueError("American odds must have magnitude at least 100")
    probability = Fraction(-odds, 100 - odds) if odds < 0 else Fraction(100, 100 + odds)
    require_probability(probability)
    return probability


def parse_american(token: str) -> int:
    token = token.strip()
    if _AMERICAN.fullmatch(token) is None:
        raise ValueError("Expected integer American odds")
    odds = int(token)
    american_probability(odds)
    return odds


def parse_probability(token: str) -> Fraction:
    token = token.strip()
    average = _AVERAGE.fullmatch(token)
    if average is None:
        return american_probability(parse_american(token))
    # Empty items, nested averages, percentages and prices cannot be odds quotes.
    values = [american_probability(parse_american(part)) for part in average[1].split(",")]
    return sum(values, Fraction(0)) / len(values)


def probit(first: Fraction | float, second: Fraction | float) -> tuple[Fraction, Fraction]:
    require_probability(first)
    require_probability(second)
    if first + second == 1:
        return Fraction(first), Fraction(second)
    z = (_NORMAL.inv_cdf(float(first)) - _NORMAL.inv_cdf(float(second))) / 2
    # erfc preserves the small tail, unlike (1 + erf(z)) / 2.
    small_tail = Fraction(math.erfc(abs(z) / math.sqrt(2)) / 2)
    result = (1 - small_tail, small_tail) if z >= 0 else (small_tail, 1 - small_tail)
    require_probability(result[0])
    require_probability(result[1])
    return result


@dataclass(frozen=True)
class Leg:
    kind: str
    market_probabilities: tuple[Fraction, Fraction]
    fair_probabilities: tuple[Fraction, Fraction]
    hold: Fraction | None = None

    @property
    def probability(self) -> Fraction:
        return self.fair_probabilities[0]


def parse_leg(token: str) -> Leg:
    sides = token.split("/")
    if len(sides) == 1:
        probability = parse_probability(sides[0])
        return Leg("fair", (probability, 1 - probability), (probability, 1 - probability))
    if len(sides) != 2:
        raise ValueError("Only two-outcome reference markets are supported")
    first = parse_probability(sides[0])
    hold_match = _HOLD.fullmatch(sides[1].strip())
    if hold_match:
        hold = Fraction(hold_match[1]) / 100
        if not 0 <= hold < 1:
            raise ValueError("Theoretical hold must be at least zero and below 100%")
        second = 1 / (1 - hold) - first
        require_probability(second)
        return Leg("hold", (first, second), probit(first, second), hold)
    second = parse_probability(sides[1])
    return Leg("pair", (first, second), probit(first, second))


def split_legs(text: str) -> list[str]:
    """Scan once: only commas outside a single avg(...) separate legs."""
    parts = []
    start = depth = 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
            if depth > 1:
                raise ValueError("Nested parentheses are unsupported")
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced parentheses")
        elif character == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    if depth or not all(parts) or len(parts) > MAX_LEGS:
        raise ValueError("Incomplete or oversized leg list")
    return parts


@dataclass(frozen=True)
class Calculation:
    legs: tuple[Leg, ...]
    offered_decimal: Fraction | None = None
    contract_quote: ContractQuote | None = None

    @property
    def probability(self) -> Fraction | None:
        if not self.legs:
            return None
        return math.prod((leg.probability for leg in self.legs), start=Fraction(1))


def parse_message(content: str) -> Calculation | None:
    """Return None for every unsupported input; never search inside chat text."""
    if not content or len(content) > MAX_MESSAGE_LENGTH:
        return None
    text = content.strip().replace("\N{MINUS SIGN}", "-")
    if not text or "\n" in text or "\r" in text:
        return None
    try:
        offered = None
        contract = None
        if ":" in text:
            if text.count(":") != 1:
                return None
            price, references = text.split(":")
            if price.strip().endswith(("c", "C", "¢")):
                contract = quote(parse_cent_price(price))
            else:
                offered = 1 / american_probability(parse_american(price))
        else:
            if text.endswith(("c", "C", "¢")):
                return Calculation((), contract_quote=quote(parse_cent_price(text)))
            references = text
        legs = tuple(parse_leg(token) for token in split_legs(references))
        if offered is None and contract is None and len(legs) == 1 and legs[0].kind == "fair":
            return None  # A lone number/average is not an automatic chat trigger.
        result = Calculation(legs, offered, contract)
        require_probability(result.probability)
        return result
    except (ValueError, ArithmeticError):
        return None


def expected_value(probability: Fraction, decimal_payout: Fraction) -> Fraction:
    if not 0 <= probability <= 1 or decimal_payout <= 1:
        raise ValueError("Invalid bet economics")
    return probability * decimal_payout - 1


def kelly_fraction(probability: Fraction, decimal_payout: Fraction, multiplier: Fraction) -> Fraction:
    if not 0 < multiplier <= 1:
        raise ValueError("Invalid Kelly multiplier")
    return expected_value(probability, decimal_payout) / (decimal_payout - 1) * multiplier


def american_from_decimal(decimal_payout: Fraction) -> Fraction:
    if decimal_payout <= 1:
        raise ValueError("Decimal payout must exceed one")
    return (decimal_payout - 1) * 100 if decimal_payout >= 2 else -100 / (decimal_payout - 1)
