"""Discord output, user settings, and worker entry point: python discord.py.

The installed discord.py library owns the import name `discord`; this file is
an executable application. Tests load it under the name `calculator_discord`.
"""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
import json
import logging
import math
import os
from pathlib import Path
import sys
import tempfile

# Resolve the installed library without importing this same-named file again.
# Register it before execution so its own `discord.*` imports work normally.
if "discord" not in sys.modules or not hasattr(sys.modules["discord"], "__path__"):
    search_paths = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
    spec = PathFinder.find_spec("discord", search_paths)
    if spec is None or spec.loader is None:
        raise ImportError("Install the discord.py dependency from requirements.txt")
    library = module_from_spec(spec)
    sys.modules["discord"] = library
    spec.loader.exec_module(library)

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from calculator import Calculation, Leg, american_from_decimal, expected_value, kelly_fraction, parse_message

logger = logging.getLogger(__name__)


# User preferences and local persistence.
KELLY_MULTIPLIERS = {"FK": Fraction(1), "HK": Fraction(1, 2), "QK": Fraction(1, 4), "EK": Fraction(1, 8)}


def bankroll_value(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Bankroll must be a nonnegative finite amount")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("Bankroll must be a nonnegative finite amount")
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError("Bankroll is outside the supported range") from error


@dataclass(frozen=True)
class UserSettings:
    bankroll: Decimal | None = None
    bankroll_enabled: bool = True
    kelly: str = "QK"

    @property
    def multiplier(self) -> Fraction:
        return KELLY_MULTIPLIERS[self.kelly]

    @property
    def visible_bankroll(self) -> Decimal | None:
        return self.bankroll if self.bankroll_enabled else None


class SettingsStore:
    def __init__(self, path: str | Path = "user_data.json"):
        self.path = Path(path)
        self._users: dict[str, UserSettings] = {}
        if self.path.exists():
            with self.path.open() as stream:
                raw = json.load(stream)
            if not isinstance(raw, dict):
                raise ValueError("Settings file must contain a JSON object")
            # Preserve valid bankroll/Kelly settings, discard obsolete devig keys.
            # Leave the on-disk file untouched until a successful settings update.
            for user_id, record in raw.items():
                if not isinstance(record, dict):
                    raise ValueError("Invalid user settings record")
                bankroll = record.get("bankroll")
                kelly = record.get("kelly", "QK")
                if not isinstance(kelly, str) or kelly not in KELLY_MULTIPLIERS:
                    kelly = "QK"
                enabled = record.get("bankroll_enabled", True)
                self._users[user_id] = UserSettings(
                    bankroll_value(bankroll) if bankroll is not None else None,
                    enabled if isinstance(enabled, bool) else True,
                    kelly,
                )

    def get(self, user_id: str) -> UserSettings:
        return self._users.get(str(user_id), UserSettings())

    def update(self, user_id: str, *, bankroll: float | None = None,
               bankroll_enabled: bool | None = None, kelly: str | None = None) -> UserSettings:
        current = self.get(user_id)
        if bankroll is not None:
            current = replace(current, bankroll=bankroll_value(bankroll))
        if bankroll_enabled is not None:
            if not isinstance(bankroll_enabled, bool):
                raise ValueError("Bankroll visibility must be true or false")
            current = replace(current, bankroll_enabled=bankroll_enabled)
        if kelly is not None:
            if kelly not in KELLY_MULTIPLIERS:
                raise ValueError("Kelly must be FK, HK, QK, or EK")
            current = replace(current, kelly=kelly)
        updated = {**self._users, str(user_id): current}
        serialized = {
            key: {"bankroll": str(value.bankroll) if value.bankroll is not None else None,
                  "bankroll_enabled": value.bankroll_enabled, "kelly": value.kelly}
            for key, value in updated.items()
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".settings-", delete=False) as stream:
                temporary = stream.name
                json.dump(serialized, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        self._users = updated
        return current


# Display formatting. Rounded values never feed back into calculations.
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


# Discord events and the /settings command.
async def handle_message(message: discord.Message, store: SettingsStore) -> None:
    if message.author.bot or message.webhook_id is not None:
        return
    calculation = parse_message(message.content)
    if calculation is None:
        return
    try:
        embed = build_embed(calculation, store.get(str(message.author.id)))
    except (ValueError, ArithmeticError):
        return
    if embed is None:
        return
    # Replies require history permission; fall back to a regular message when
    # unavailable. Both paths suppress mentions and send only a single result.
    can_reply = True
    if message.guild is not None:
        permissions = message.channel.permissions_for(message.guild.me)
        can_send = permissions.send_messages_in_threads if isinstance(message.channel, discord.Thread) else permissions.send_messages
        if not can_send or not permissions.embed_links:
            return
        can_reply = permissions.read_message_history
    try:
        if can_reply:
            await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.warning("Discord rejected a calculator response", exc_info=True)


class CalculatorBot(commands.Bot):
    def __init__(self, store: SettingsStore):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="/", intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.store = store

    async def setup_hook(self) -> None:
        # Sync once per connection lifecycle, not on every gateway reconnect.
        # This removes the old global /ev command and obsolete settings option.
        await self.tree.sync()
        logger.info("Synced /settings; calculation commands are chat-only")

    async def on_ready(self) -> None:
        logger.info("Calculator connected as %s", self.user)
        await self.change_presence(activity=discord.Game(name="powered by JOVEL"))

    async def on_message(self, message: discord.Message) -> None:
        try:
            await handle_message(message, self.store)
        except Exception:
            # Unexpected failures belong in operator logs, never public chat.
            logger.exception("Calculator message handler failed")


def create_bot(store: SettingsStore) -> CalculatorBot:
    bot = CalculatorBot(store)

    @bot.tree.command(name="settings", description="Manage bankroll and Kelly preferences")
    @app_commands.describe(bankroll="Set bankroll amount", toggle_bankroll="Show or hide wager amounts", kelly="Choose Kelly fraction")
    @app_commands.choices(kelly=[app_commands.Choice(name=key, value=key) for key in KELLY_MULTIPLIERS])
    async def settings(interaction: discord.Interaction, bankroll: float | None = None,
                       toggle_bankroll: bool | None = None, kelly: str | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            if bankroll is None and toggle_bankroll is None and kelly is None:
                preference = store.get(str(interaction.user.id))
            else:
                preference = store.update(str(interaction.user.id), bankroll=bankroll,
                                          bankroll_enabled=toggle_bankroll, kelly=kelly)
            amount = money(preference.bankroll) if preference.bankroll is not None else "Not set"
            visibility = "Enabled" if preference.bankroll_enabled else "Disabled"
            await interaction.followup.send(
                f"Bankroll: {amount}\nWager amounts: {visibility}\nKelly: {preference.kelly}", ephemeral=True)
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
        except OSError:
            logger.exception("Could not save preferences")
            await interaction.followup.send("Could not save settings. Please try again later.", ephemeral=True)

    return bot


# Worker startup. Importing the file never connects to Discord.
def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is required")
    logging.info("Starting probit-pm-v1; revision=%s", os.getenv("APP_REVISION", "unspecified"))
    store = SettingsStore(os.getenv("USER_DATA_FILE", "user_data.json"))
    create_bot(store).run(token)


if __name__ == "__main__":
    main()
