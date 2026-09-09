"""Discord presentation; rounded strings never feed back into the calculator."""

from decimal import Decimal
from fractions import Fraction
import math

import discord

from calculator import Calculation, Leg, american_from_decimal, expected_value, kelly_fraction
from user_settings import UserSettings


def format_odds(decimal_payout: Fraction) -> str:
    odds = round(american_from_decimal(decimal_payout))
    if odds == -100:
        odds = 100
    return f"{odds:+d}"


def percent(value: Fraction) -> str:
    number = float(value * 100)
    if not math.isfinite(number):
        raise ValueError("Percentage is outside the display range")
    return f"{0 if abs(number) < 0.005 else number:.2f}%"


def money(value: Fraction | Decimal) -> str:
    if isinstance(value, Fraction):
        value = Decimal(value.numerator) / Decimal(value.denominator)
    return f"${value:,.2f}"


def code_block(lines: list[str], *, color_rows: bool = False) -> str:
    if color_rows:
        lines = [lines[0], *(f"\x1b[0;33m{line}\x1b[0m" for line in lines[1:])]
    return "```ansi\n" + "\n".join(lines) + "\n```"


def leg_table(leg: Leg) -> str:
    columns = []
    for probabilities in (leg.market_probabilities, leg.fair_probabilities):
        odds = [format_odds(1 / p) for p in probabilities]
        width = max(4, *(len(value) for value in odds))
        columns.append([f"{percent(p):>6}: {odd:>{width}}" for p, odd in zip(probabilities, odds)])
    left_width = max(len("OG Odds"), *(len(line) for line in columns[0]))
    lines = [f"{'OG Odds':<{left_width}}    Fair Odds"]
    lines.extend(f"{left:<{left_width}}    {right}" for left, right in zip(*columns))
    return code_block(lines, color_rows=True)


def build_embed(calculation: Calculation, settings: UserSettings) -> discord.Embed | None:
    embed = discord.Embed(color=0x000000)
    probability = calculation.probability
    lines = []
    footer = []
    contract = calculation.contract_quote
    if contract:
        cents = format(Decimal(contract.price_cc) / 100, "f").rstrip("0").rstrip(".") if contract.price_cc % 100 else str(contract.price_cc // 100)
        embed.title = f"Kalshi: {cents}¢"
        payouts = [("Maker", contract.maker_decimal), ("Taker", contract.taker_decimal)]
        odds_width = max(len(format_odds(payout)) for _, payout in payouts)
        for label, payout in payouts:
            row = f"{label}: {format_odds(payout):>{odds_width}}"
            if probability is not None:
                ev = expected_value(probability, payout)
                kelly = kelly_fraction(probability, payout, settings.multiplier)
                row += f"    EV: {percent(ev):>7}    {settings.kelly}: {percent(kelly):>6}"
            else:
                row += "  (no fee)" if label == "Maker" else "  (100 contracts)"
            lines.append(row)
        lines.append("")
        if probability is not None:
            lines.append(f"FV: {format_odds(1 / probability)}    WIN: {percent(probability)}")
        lines.append(f"Fee: {money(Fraction(contract.taker_fee_cents, 100))} / 100 contracts")
        if probability is not None and settings.visible_bankroll is not None:
            lines.extend(["", f"Est. total outlay ({settings.kelly}, incl. fees)"])
            for label, payout in payouts:
                stake = kelly_fraction(probability, payout, settings.multiplier) * Fraction(settings.visible_bankroll)
                lines.append(f"{label}: {money(stake)}")
        footer.append("100-contract estimate · Maker assumes no fee")
    elif calculation.offered_decimal is not None:
        payout = calculation.offered_decimal
        ev = expected_value(probability, payout)
        kelly = kelly_fraction(probability, payout, settings.multiplier)
        lines.extend([
            f"Bet Odds: {format_odds(payout)}",
            f"EV: {percent(ev)}    {settings.kelly}: {percent(kelly)}",
            f"FV: {format_odds(1 / probability)}    WIN: {percent(probability)}",
        ])
        if settings.visible_bankroll is not None:
            lines.append(f"Wager ({settings.kelly}): {money(kelly * Fraction(settings.visible_bankroll))}")
    elif len(calculation.legs) > 1:
        lines.extend(["Combined Fair Odds", f"FV: {format_odds(1 / probability)}    WIN: {percent(probability)}"])

    if lines:
        embed.description = code_block(lines)
    for index, leg in enumerate(calculation.legs, 1):
        label = f"Leg {index}" if len(calculation.legs) > 1 else "\u200b"
        if leg.hold is not None:
            hold_text = format(Decimal(leg.hold.numerator * 100) / Decimal(leg.hold.denominator), "f")
            prefix = f"Leg {index} · " if len(calculation.legs) > 1 else ""
            label = f"{prefix}{hold_text}% theoretical hold · Opposite estimated"
        embed.add_field(name=label, value=leg_table(leg), inline=False)
    if len(calculation.legs) > 1:
        footer.append("Independent legs")
    if footer:
        embed.set_footer(text=" · ".join(footer))
    # Never send a partial result or let user-generated output exceed Discord limits.
    if (len(embed) > 6000 or len(embed.description or "") > 4096 or len(embed.title or "") > 256
            or len(embed.fields) > 25
            or any(len(field.name) > 256 or len(field.value) > 1024 for field in embed.fields)):
        return None
    return embed
