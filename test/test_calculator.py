import json
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
        for text in ['47c', '97.4C', '1.01c', '99c', '40c:-250/180',
                     '40c:-130/4%', '−198:−250/180', '100:AVG(-110,+110)',
                     '100:avg(-110)/avg(110)', '-130/4.5%',
                     '-130,-132', '100,200', '300:avg(-110,-120)/110,-130/4%,120']:
            with self.subTest(text=text):
                self.assertIsNotNone(parse_message(text))
        self.assertEqual(parse_message('100:AVG(-110,+110)').probability, Fraction(1, 2))
        result = parse_message('300:100,200')
        self.assertEqual(result.probability, Fraction(1, 6))

    def test_silent_rejections(self):
        for text in ['', 'hello 47c', '47c please', '100', 'avg(100)', '40c:',
                     ':134', '40c:134:100', '0c', '100c', '97.401c', '.5c',
                     '-130/100%', '-130/-4%', '+2500/4%', '-130/110/200',
                     '100:avg()', '100:avg(100,)', '100:avg(avg(100))',
                     '100:100,', '100:,100', '100:100,,200', '100:(100)',
                     '100:99', '100:NaN', '40c\n:134', '47c!',
                     ','.join(['100']*21), '1'*2001]:
            with self.subTest(text=text):
                self.assertIsNone(parse_message(text))

    def test_negative_ev_and_output_bound(self):
        result = parse_message('100:200')
        self.assertEqual(expected_value(result.probability, result.offered_decimal), Fraction(-1, 3))
        self.assertEqual(kelly_fraction(result.probability, result.offered_decimal, Fraction(1, 4)), 0)
        result = parse_message(','.join(['-250/180'] * 20))
        embed = build_embed(result, UserSettings())
        self.assertTrue(embed is None or len(embed) <= 6000)

    def test_display_and_bankroll(self):
        rendered = str(build_embed(parse_message('40c:134'), UserSettings(bankroll=Decimal('1000'))).to_dict())
        for expected in ['+150', '+140', '6.84%', '2.53%', '1.14%', '0.45%', '$1.68', '100 contracts', 'incl. fees']:
            self.assertIn(expected, rendered)
        self.assertIn('-10653', str(build_embed(parse_message('99c'), UserSettings()).to_dict()))
        self.assertIn('+100', str(build_embed(parse_message('100:100'), UserSettings()).to_dict()))
        standalone = str(build_embed(parse_message('100,200'), UserSettings(bankroll=Decimal('1000'))).to_dict())
        self.assertNotIn('EV:', standalone)
        self.assertNotIn('$', standalone)


class SettingsTests(unittest.TestCase):
    def test_migration_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'user_data.json'
            path.write_text(json.dumps({'1': {'bankroll': 1234.56, 'kelly': 'HK', 'devig_method': 'power'}}))
            original = path.read_text()
            store = SettingsStore(path)
            self.assertTrue(store.get('1').bankroll_enabled)
            self.assertEqual(store.get('1').bankroll, Decimal('1234.56'))
            self.assertEqual(path.read_text(), original)
            with patch('calculator_discord.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.update('1', bankroll=20)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(store.get('1').bankroll, Decimal('1234.56'))
            store.update('2', bankroll=100)
            self.assertNotIn('devig_method', path.read_text())
            self.assertEqual(SettingsStore(path).get('1').kelly, 'HK')
            for value in [-1, float('inf'), float('nan')]:
                with self.assertRaises(ValueError):
                    store.update('1', bankroll=value)


class DiscordTests(unittest.IsolatedAsyncioTestCase):
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
            self.assertNotIn('devig_method', [p.name for p in bot.tree.get_commands()[0].parameters])
            await bot.close()


if __name__ == '__main__':
    unittest.main()
