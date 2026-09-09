"""Discord gateway adapter. Creating a bot does not connect or load credentials."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from calculator import parse_message
from presentation import build_embed, money
from user_settings import KELLY_MULTIPLIERS, SettingsStore

logger = logging.getLogger(__name__)


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
