import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_CEILING
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


from calculator import parse_message, expected_value, kelly_fraction, probit
from calculator import quote, taker_fee_cents

# discord.py is the executable; `discord` itself names the installed library.
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("calculator_discord", Path(__file__).resolve().parents[1] / "discord.py")
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)
discord = app.discord
build_embed = app.build_embed
SettingsStore = app.SettingsStore
UserSettings = app.UserSettings
create_bot = app.create_bot
handle_message = app.handle_message


class CalculationTests(unittest.TestCase):
    def test_acceptance_probabilities_and_ev(self):
        cases = [('-250/180', .6794021237469998, None),
                 ('-198:-250/180', .6794021237469998, .022534509477808),
                 ('100:-130/110', .5445740001510062, .0891480003020124),
                 ('-130/4%', .5444451797588348, None)]
        for text, probability, ev in cases:
            with self.subTest(text=text):
                result = parse_message(text)
                self.assertAlmostEqual(float(result.probability), probability, places=13)
                if ev is not None:
                    self.assertAlmostEqual(float(expected_value(result.probability, result.offered_decimal)), ev, places=13)
        result = parse_message('-198:-250/180')
        self.assertAlmostEqual(float(kelly_fraction(result.probability, result.offered_decimal, Fraction(1, 4))), .011154582191515, places=13)

    def test_prediction_acceptance(self):
        q = quote(4700)
        self.assertEqual(q.taker_fee_cents, 175)
        self.assertEqual(q.maker_decimal, Fraction(100, 47))
        self.assertEqual(q.taker_decimal, Fraction(80, 39))
        result = parse_message('40c:134')
        q = result.contract_quote
        self.assertEqual(q.taker_fee_cents, 168)
        self.assertAlmostEqual(float(expected_value(result.probability, q.maker_decimal)), .068376068376, places=11)
        self.assertAlmostEqual(float(expected_value(result.probability, q.taker_decimal)), .025312925504864085, places=11)
        self.assertAlmostEqual(float(kelly_fraction(result.probability, q.taker_decimal, Fraction(1, 4))), .004522645469, places=11)

    def test_every_price_against_decimal_oracle(self):
        for cc in range(100, 9901):
            p = Decimal(cc) / 10000
            expected = int((Decimal('0.07') * 100 * p * (1-p) * 100).to_integral_value(rounding=ROUND_CEILING))
            self.assertEqual(taker_fee_cents(100, cc), expected, cc)
        self.assertEqual(taker_fee_cents(1, 4700), 2)

    def test_hold_convention_and_symmetry(self):
        result = parse_message('-130/4%').legs[0]
        self.assertEqual(sum(result.market_probabilities), Fraction(25, 24))
        self.assertEqual(sum(result.fair_probabilities), 1)
        self.assertEqual(probit(*reversed(result.market_probabilities)), tuple(reversed(result.fair_probabilities)))
        self.assertAlmostEqual(float(parse_message('-200/30%').probability), .4439911139, places=9)
        self.assertEqual(parse_message('-130/0%').probability, Fraction(13, 23))

    def test_accepted_grammar(self):
        for text in ['47c', '47¢', '97.4C', '1.01c', '99c', '40c:-250/180',
                     '40c:-130/4%', '−198:−250/180', '100:AVG(-110,+110)',
                     '100:avg(-110)/avg(110)', '-130/4.5%',
                     '-130,-132', '100,200', '300:avg(-110,-120)/110,-130/4%,120']:
            with self.subTest(text=text):
                self.assertIsNotNone(parse_message(text))
        self.assertEqual(parse_message('100:AVG(-110,+110)').probability, Fraction(1, 2))
        result = parse_message('300:100,200')
        self.assertEqual(result.probability, Fraction(1, 6))

    def test_generated_legacy_colon_expressions_remain_parseable(self):
        odds = [-500, -250, -110, 100, 134, 200, 500]
        for offered in odds:
            for fair in odds:
                with self.subTest(offered=offered, fair=fair):
                    self.assertIsNotNone(parse_message(f'{offered}:{fair}'))
            for first in odds:
                for second in odds:
                    with self.subTest(offered=offered, first=first, second=second):
                        self.assertIsNotNone(
                            parse_message(f'{offered}:{first}/{second}')
                        )

    def test_silent_rejections(self):
        for text in ['', 'hello 47c', '47c please', '100', 'avg(100)', '40c:',
                     ':134', '40c:134:100', '0c', '100c', '97.401c', '.5c',
                     '-130/100%', '-130/-4%', '+2500/4%', '-130/110/200',
                     '100:avg()', '100:avg(100,)', '100:avg(avg(100))',
                     '100:100,', '100:,100', '100:100,,200', '100:(100)',
                     '100:99', '100:NaN', '40c\n:134', '47c!', '35 c',
                     ','.join(['100']*21), '1'*2001]:
            with self.subTest(text=text):
                self.assertIsNone(parse_message(text))

    def test_negative_ev_and_output_bound(self):
        result = parse_message('100:200')
        self.assertEqual(expected_value(result.probability, result.offered_decimal), Fraction(-1, 3))
        self.assertEqual(kelly_fraction(result.probability, result.offered_decimal, Fraction(1, 4)), Fraction(-1, 12))
        result = parse_message(','.join(['-250/180'] * 20))
        embed = build_embed(result, UserSettings())
        self.assertTrue(embed is None or len(embed) <= 6000)

    def test_display_templates_and_extreme_widths(self):
        embed = build_embed(parse_message('40c:134'), UserSettings())
        rendered = str(embed.to_dict())
        for expected in ['Kalshi: 40¢', '+150', '+140', '6.84%', '2.53%', '1.14%', '0.45%', '$1.68', 'contracts']:
            self.assertIn(expected, rendered)
        for removed in ['WIN:', '100-contract estimate', 'Maker assumes', 'Wager', 'bankroll']:
            self.assertNotIn(removed, rendered)
        self.assertIn('\x1b[1;33m', embed.description)
        self.assertIn('-10653', str(build_embed(parse_message('99c'), UserSettings()).to_dict()))
        self.assertIn('+100', str(build_embed(parse_message('100:100'), UserSettings()).to_dict()))
        standalone = build_embed(parse_message('100,200'), UserSettings()).to_dict()
        self.assertEqual(standalone['title'], 'Fair Odds: +500')
        standalone = str(standalone)
        self.assertNotIn('EV:', standalone)
        self.assertNotIn('$', standalone)

    def test_approved_compact_templates(self):
        def plain(description):
            return re.sub(r'\x1b\[[0-9;]*m', '', description)

        standalone = build_embed(parse_message('35c'), UserSettings())
        self.assertEqual(standalone.title, 'Kalshi: 35¢')
        self.assertEqual(plain(standalone.description), (
            '```ansi\n'
            'Maker: +186  (no fee)\n'
            'Taker: +173  (100 contracts)\n'
            'Fee:   $1.60\n'
            '```'
        ))

        comparison = build_embed(parse_message('35c:175'), UserSettings())
        self.assertEqual(comparison.title, 'Kalshi: 35¢')
        self.assertEqual(plain(comparison.description), (
            '```ansi\n'
            'Maker: +186    EV:  3.90%    QK:  0.52%\n'
            'Taker: +173    EV: -0.65%    QK: -0.09%\n'
            '\n'
            'FV: +175    Fee: $1.60 / 100 contracts\n'
            '```'
        ))

        offered = build_embed(parse_message('250:100'), UserSettings())
        self.assertEqual(offered.title, 'Odds: +250')
        self.assertEqual(plain(offered.description), (
            '```ansi\nEV: 75.00%    QK: 7.50%\nFV: +100\n```'
        ))

        hold = build_embed(parse_message('250/8%'), UserSettings()).to_dict()
        self.assertNotIn('title', hold)
        self.assertNotIn('footer', hold)
        self.assertNotIn('theoretical hold', str(hold))


class SettingsTests(unittest.TestCase):
    def test_migration_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'user_data.json'
            path.write_text(json.dumps({'1': {'bankroll': 1234.56, 'kelly': 'HK', 'devig_method': 'power'}}))
            original = path.read_text()
            store = SettingsStore(path)
            self.assertEqual(store.get('1').kelly, 'HK')
            self.assertEqual(path.read_text(), original)
            with patch('calculator_discord.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.update('1', kelly='FK')
            self.assertEqual(path.read_text(), original)
            self.assertEqual(store.get('1').kelly, 'HK')
            store.update('2', kelly='EK')
            self.assertNotIn('devig_method', path.read_text())
            self.assertNotIn('bankroll', path.read_text())
            self.assertEqual(SettingsStore(path).get('1').kelly, 'HK')
            for value in ['BAD', '', None]:
                with self.assertRaises(ValueError):
                    store.update('1', kelly=value)


class DiscordTests(unittest.IsolatedAsyncioTestCase):
    def make_message(self, content, *, permissions=None):
        permissions = permissions or SimpleNamespace(
            send_messages=True,
            embed_links=True,
            read_message_history=True,
        )
        return SimpleNamespace(
            author=SimpleNamespace(bot=False, id=1),
            webhook_id=None,
            content=content,
            guild=SimpleNamespace(me=object()),
            reply=AsyncMock(),
            channel=SimpleNamespace(
                permissions_for=lambda _: permissions,
                send=AsyncMock(),
            ),
        )

    async def test_every_chat_family_reaches_discord_reply(self):
        expected_text = {
            '-250/180': ['67.94%', '-212', '+212'],
            '-198:-250/180': ['2.25%', '1.12%', '-198'],
            '-130/4%': ['54.44%', '-120'],
            '47c': ['Kalshi: 47¢', '+113', '+105', '$1.75'],
            '40c:134': ['6.84%', '2.53%', '$1.68'],
            '300:-250/180,-130/4%,120': ['Leg 1', 'Leg 2', 'Leg 3', 'WIN:'],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / 'settings.json')
            for content, fragments in expected_text.items():
                with self.subTest(content=content):
                    message = self.make_message(content)
                    await handle_message(message, store)
                    message.reply.assert_awaited_once()
                    embed = message.reply.call_args.kwargs['embed']
                    rendered = str(embed.to_dict())
                    for fragment in fragments:
                        self.assertIn(fragment, rendered)
                    self.assertFalse(message.reply.call_args.kwargs['mention_author'])
                    self.assertEqual(
                        message.reply.call_args.kwargs['allowed_mentions'].to_dict(),
                        {'parse': []},
                    )

    async def test_bot_event_routes_chat_to_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = create_bot(SettingsStore(Path(directory) / 'settings.json'))
            message = self.make_message('-250/180')
            await bot.on_message(message)
            message.reply.assert_awaited_once()
            await bot.close()

    async def test_reply_fallback_and_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / 'settings.json')
            permissions = SimpleNamespace(send_messages=True, embed_links=True, read_message_history=True)
            message = SimpleNamespace(author=SimpleNamespace(bot=False, id=1), webhook_id=None,
                content='40c:134', guild=SimpleNamespace(me=object()), reply=AsyncMock(),
                channel=SimpleNamespace(permissions_for=lambda _: permissions, send=AsyncMock()))
            await handle_message(message, store)
            message.reply.assert_awaited_once()
            self.assertFalse(message.reply.call_args.kwargs['mention_author'])
            permissions.read_message_history = False
            await handle_message(message, store)
            message.channel.send.assert_awaited_once()
            message.content = 'can someone explain 40c:134?'
            await handle_message(message, store)
            message.channel.send.assert_awaited_once()
            message.content = '47c'
            message.author.bot = True
            await handle_message(message, store)
            message.channel.send.assert_awaited_once()

    async def test_missing_permissions_and_webhooks_are_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / 'settings.json')
            permissions = SimpleNamespace(
                send_messages=True,
                embed_links=False,
                read_message_history=True,
            )
            message = self.make_message('47c', permissions=permissions)
            await handle_message(message, store)
            message.reply.assert_not_awaited()
            message.channel.send.assert_not_awaited()

            permissions.embed_links = True
            message.webhook_id = 123
            await handle_message(message, store)
            message.reply.assert_not_awaited()
            message.channel.send.assert_not_awaited()

    async def test_thread_permission_and_failed_send(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / 'settings.json')
            channel = Mock(spec=discord.Thread)
            channel.permissions_for.return_value = SimpleNamespace(
                send_messages=False, send_messages_in_threads=True,
                embed_links=True, read_message_history=True)
            channel.send = AsyncMock()
            response = SimpleNamespace(status=403, reason='Forbidden')
            message = SimpleNamespace(author=SimpleNamespace(bot=False, id=1), webhook_id=None,
                content='47c', guild=SimpleNamespace(me=object()), channel=channel,
                reply=AsyncMock(side_effect=discord.Forbidden(response, 'Missing permission')))
            with self.assertLogs('calculator_discord', level='WARNING'):
                await handle_message(message, store)
            message.reply.assert_awaited_once()
            channel.send.assert_not_awaited()

    async def test_only_settings_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = create_bot(SettingsStore(Path(directory) / 'settings.json'))
            self.assertEqual([command.name for command in bot.tree.get_commands()], ['settings'])
            self.assertEqual([p.name for p in bot.tree.get_commands()[0].parameters], ['kelly'])
            await bot.close()

    async def test_settings_command_reads_and_updates_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            store = SettingsStore(path)
            bot = create_bot(store)
            command = bot.tree.get_command('settings')
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=42),
                response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )

            await command.callback(interaction, kelly='HK')
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            sent = interaction.followup.send.call_args
            self.assertEqual(sent.args[0], 'Kelly: HK')
            self.assertTrue(sent.kwargs['ephemeral'])
            self.assertEqual(SettingsStore(path).get('42').kelly, 'HK')

            interaction.response.defer.reset_mock()
            interaction.followup.send.reset_mock()
            await command.callback(interaction)
            self.assertEqual(interaction.followup.send.call_args.args[0], 'Kelly: HK')
            await bot.close()

    async def test_startup_hooks_sync_settings_and_restore_custom_status(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = create_bot(SettingsStore(Path(directory) / 'settings.json'))
            with patch.object(bot.tree, 'sync', new=AsyncMock()) as sync:
                await bot.setup_hook()
                sync.assert_awaited_once_with()
            with patch.object(bot, 'change_presence', new=AsyncMock()) as change_presence:
                await bot.on_ready()
                activity = change_presence.call_args.kwargs['activity']
                self.assertEqual(activity.name, 'powered by JOVEL')
                self.assertEqual(activity.type, discord.ActivityType.custom)
            await bot.close()


class StartupTests(unittest.TestCase):
    def test_discord_file_runs_directly_despite_library_name_collision(self):
        environment = os.environ.copy()
        environment.pop('DISCORD_BOT_TOKEN', None)
        result = subprocess.run(
            [sys.executable, 'discord.py'],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), 'DISCORD_BOT_TOKEN is required')

    def test_main_constructs_worker_without_connecting_during_test(self):
        worker = SimpleNamespace(run=Mock())
        store = object()
        with (patch.dict(os.environ, {'DISCORD_BOT_TOKEN': 'test-token'}, clear=True),
              patch('calculator_discord.load_dotenv'),
              patch('calculator_discord.SettingsStore', return_value=store),
              patch('calculator_discord.create_bot', return_value=worker)):
            app.main()
        worker.run.assert_called_once_with('test-token')

    def test_main_requires_token(self):
        with (patch.dict(os.environ, {}, clear=True),
              patch('calculator_discord.load_dotenv')):
            with self.assertRaisesRegex(SystemExit, 'DISCORD_BOT_TOKEN is required'):
                app.main()


if __name__ == '__main__':
    unittest.main()
