"""Discord output, user settings, and worker entry point: python discord.py.

The installed discord.py library owns the import name `discord`; this file is
an executable application. Tests load it under the name `calculator_discord`.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
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


# Kelly preferences and local persistence.
KELLY_MULTIPLIERS = {"FK": Fraction(1), "HK": Fraction(1, 2), "QK": Fraction(1, 4), "EK": Fraction(1, 8)}


@dataclass(frozen=True)
class UserSettings:
    kelly: str = "QK"

    @property
    def multiplier(self) -> Fraction:
        return KELLY_MULTIPLIERS[self.kelly]


class SettingsStore:
    def __init__(self, path: str | Path = "user_data.json"):
        self.path = Path(path)
        self._users: dict[str, UserSettings] = {}
        if self.path.exists():
            with self.path.open() as stream:
                raw = json.load(stream)
            if not isinstance(raw, dict):
                raise ValueError("Settings file must contain a JSON object")
            # Preserve valid Kelly settings and discard obsolete fields on update.
            for user_id, record in raw.items():
                if not isinstance(record, dict):
                    raise ValueError("Invalid user settings record")
                kelly = record.get("kelly", "QK")
                if not isinstance(kelly, str) or kelly not in KELLY_MULTIPLIERS:
                    kelly = "QK"
                self._users[user_id] = UserSettings(kelly)

    def get(self, user_id: str) -> UserSettings:
        return self._users.get(str(user_id), UserSettings())

    def update(self, user_id: str, *, kelly: str) -> UserSettings:
        current = self.get(user_id)
        if kelly not in KELLY_MULTIPLIERS:
            raise ValueError("Kelly must be FK, HK, QK, or EK")
        current = replace(current, kelly=kelly)
        updated = {**self._users, str(user_id): current}
        serialized = {key: {"kelly": value.kelly} for key, value in updated.items()}
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


def highlight(value: str, width: int = 0) -> str:
    return f"\x1b[1;33m{value:>{width}}\x1b[0m"


def code_block(lines: list[str]) -> str:
    return "```ansi\n" + "\n".join(lines) + "\n```"


def leg_table(leg: Leg) -> str:
    columns = []
    for probabilities in (leg.market_probabilities, leg.fair_probabilities):
        odds = [format_odds(1 / p) for p in probabilities]
        width = max(4, *(len(value) for value in odds))
        columns.append([f"{percent(p):>6}: {odd:>{width}}" for p, odd in zip(probabilities, odds)])
    left_width = max(len("OG Odds"), *(len(line) for line in columns[0]))
    lines = [f"{'OG Odds':<{left_width}}    Fair Odds"]
    lines.extend(highlight(f"{left:<{left_width}}    {right}") for left, right in zip(*columns))
    return code_block(lines)


def build_embed(calculation: Calculation, settings: UserSettings) -> discord.Embed | None:
    embed = discord.Embed(color=0x000000)
    probability = calculation.probability
    lines = []
    contract = calculation.contract_quote
    if contract:
        cents = format(Decimal(contract.price_cc) / 100, "f").rstrip("0").rstrip(".") if contract.price_cc % 100 else str(contract.price_cc // 100)
        embed.title = f"Kalshi: {cents}¢"
        payouts = [("Maker", contract.maker_decimal), ("Taker", contract.taker_decimal)]
        odds_width = max(len(format_odds(payout)) for _, payout in payouts)
        if probability is None:
            lines.extend([
                f"Maker: {highlight(format_odds(contract.maker_decimal), odds_width)}  (no fee)",
                f"Taker: {highlight(format_odds(contract.taker_decimal), odds_width)}  ({highlight('100')} contracts)",
                f"Fee:   {highlight(money(Fraction(contract.taker_fee_cents, 100)))}",
            ])
        else:
            rows = []
            for label, payout in payouts:
                ev = percent(expected_value(probability, payout))
                kelly = percent(kelly_fraction(probability, payout, settings.multiplier))
                rows.append((label, format_odds(payout), ev, kelly))
            ev_width = max(len(row[2]) for row in rows)
            kelly_width = max(len(row[3]) for row in rows)
            for label, odds, ev, kelly in rows:
                lines.append(
                    f"{label}: {highlight(odds, odds_width)}    "
                    f"EV: {highlight(ev, ev_width)}    "
                    f"{settings.kelly}: {highlight(kelly, kelly_width)}"
                )
            lines.append("")
            summary = f"FV: {highlight(format_odds(1 / probability))}"
            if len(calculation.legs) > 1:
                summary += f"    WIN: {highlight(percent(probability))}"
            summary += (
                f"    Fee: {highlight(money(Fraction(contract.taker_fee_cents, 100)))}"
                f" / {highlight('100')} contracts"
            )
            lines.append(summary)
    elif calculation.offered_decimal is not None:
        payout = calculation.offered_decimal
        ev = expected_value(probability, payout)
        kelly = kelly_fraction(probability, payout, settings.multiplier)
        embed.title = f"Odds: {format_odds(payout)}"
        lines.extend([
            f"EV: {highlight(percent(ev))}    {settings.kelly}: {highlight(percent(kelly))}",
            f"FV: {highlight(format_odds(1 / probability))}",
        ])
        if len(calculation.legs) > 1:
            lines[-1] += f"    WIN: {highlight(percent(probability))}"
    elif len(calculation.legs) > 1:
        embed.title = f"Fair Odds: {format_odds(1 / probability)}"
        lines.append(f"WIN: {highlight(percent(probability))}")

    if lines:
        embed.description = code_block(lines)
    for index, leg in enumerate(calculation.legs, 1):
        label = f"Leg {index}" if len(calculation.legs) > 1 else "\u200b"
        embed.add_field(name=label, value=leg_table(leg), inline=False)
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
        self._checked_command_guilds: set[int] = set()

    async def setup_hook(self) -> None:
        # Sync once per connection lifecycle, not on every gateway reconnect.
        # This removes the old global /ev command and obsolete settings option.
        commands = await self.tree.sync()
        logger.info("Registered global commands: %s", {
            command.name: [option.name for option in command.options] for command in commands
        })

    async def remove_legacy_commands(self, guild: discord.Guild) -> None:
        if guild.id in self._checked_command_guilds:
            return
        try:
            for command in await self.tree.fetch_commands(guild=guild):
                if command.type == discord.AppCommandType.chat_input and command.name in {"ev", "settings"}:
                    await command.delete()
                    logger.info("Removed legacy guild /%s in %s", command.name, guild.id)
            self._checked_command_guilds.add(guild.id)
        except discord.HTTPException:
            logger.exception("Could not clean legacy commands in %s", guild.id)

    async def on_ready(self) -> None:
        logger.info("Calculator connected as %s", self.user)
        for guild in self.guilds:
            await self.remove_legacy_commands(guild)
        await self.change_presence(
            activity=discord.Activity(
                name="powered by JOVEL",
                type=discord.ActivityType.custom,
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.remove_legacy_commands(guild)

    async def on_message(self, message: discord.Message) -> None:
        try:
            await handle_message(message, self.store)
        except Exception:
            # Unexpected failures belong in operator logs, never public chat.
            logger.exception("Calculator message handler failed")


def create_bot(store: SettingsStore) -> CalculatorBot:
    bot = CalculatorBot(store)

    @bot.tree.command(name="settings", description="Choose a Kelly fraction")
    @app_commands.describe(kelly="Choose Kelly fraction")
    @app_commands.choices(kelly=[app_commands.Choice(name=key, value=key) for key in KELLY_MULTIPLIERS])
    async def settings(interaction: discord.Interaction, kelly: str | None = None):
        await interaction.response.defer(ephemeral=True)
        try:
            preference = store.get(str(interaction.user.id)) if kelly is None else store.update(
                str(interaction.user.id), kelly=kelly
            )
            await interaction.followup.send(f"Kelly: {preference.kelly}", ephemeral=True)
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
