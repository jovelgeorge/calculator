import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import re
import json
from typing import List, Dict, Union, Tuple
from datetime import datetime
import numpy as np
from scipy import stats
from enum import Enum

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

EMBED_COLOR = 0x000000
PADDING = ' ' * 4
USER_DATA_FILE = 'user_data.json'

class KellyType(Enum):
    FK = 1
    HK = 0.5
    QK = 0.25
    EK = 0.125

class DevigMethod(Enum):
    wc = "worst-case (default)"
    power = "power"
    probit = "probit"
    tko = "tko"
    goto = "goto"

def american_to_decimal(odds: int) -> float:
    return (odds / 100) + 1 if odds > 0 else (100 / abs(odds)) + 1

def implied_probability(odds: int) -> float:
    return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)

def decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds == 1:
        return 0
    elif decimal_odds >= 2:
        return int((decimal_odds - 1) * 100)
    else:
        return int(-100 / (decimal_odds - 1))

EXACT_FAIR_LEG_RE = re.compile(r'^[+-]?\d+$')
PAIR_LEG_RE = re.compile(r'^([+-]?\d+)/([+-]?\d+)$')
HOLD_LEG_RE = re.compile(r'^([+-]?\d+)/(\d+)%$')
AVG_RE = re.compile(r'avg\([^)]+\)')
LEG_SPLIT_RE = re.compile(r',\s*(?![^()]*\))')

def parse_american_odds(value: Union[str, int]) -> int:
    try:
        odds = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid American odds: {value}") from exc

    if -100 < odds < 100:
        raise ValueError(f"Invalid American odds: {value}. Use odds <= -100 or >= 100.")
    return odds

def parse_devig_method(value: Union[str, DevigMethod]) -> DevigMethod:
    if isinstance(value, DevigMethod):
        return value

    method = str(value).strip()
    if method in DevigMethod.__members__:
        return DevigMethod[method]

    for devig_method in DevigMethod:
        if method == devig_method.value:
            return devig_method

    raise ValueError(f"Invalid devig method: {value}. Valid options are: {', '.join(DevigMethod.__members__.keys())}")

def get_saved_devig_method(user_settings: Dict) -> Union[DevigMethod, None]:
    saved_method = user_settings.get("devig_method")
    return parse_devig_method(saved_method) if saved_method is not None else None

def substitute_avg(odds_str: str) -> str:
    return AVG_RE.sub(lambda match: str(parse_avg(match.group())), odds_str)

def american_from_probability(probability: float) -> int:
    return decimal_to_american(1 / probability)

def build_leg_result(
    market_odds1: int,
    market_odds2: int,
    fair_prob1: float,
    fair_prob2: float,
) -> Dict[str, Union[int, float]]:
    return {
        'market_odds': market_odds1,
        'market_odds2': market_odds2,
        'market_prob': implied_probability(market_odds1),
        'market_prob2': implied_probability(market_odds2),
        'fair_odds': american_from_probability(fair_prob1),
        'fair_odds2': american_from_probability(fair_prob2),
        'win': fair_prob1,
        'fair_prob2': fair_prob2,
    }

def parse_leg(token: str, effective_devig_method: Union[DevigMethod, None]) -> Tuple[Dict[str, Union[int, float]], float]:
    token = token.strip()

    if EXACT_FAIR_LEG_RE.fullmatch(token):
        fair_odds = parse_american_odds(token)
        fair_prob = implied_probability(fair_odds)
        result = {
            'market_odds': fair_odds,
            'fair_odds': american_from_probability(fair_prob),
            'win': fair_prob,
        }
        return result, fair_prob

    hold_match = HOLD_LEG_RE.fullmatch(token)
    if hold_match:
        target_odds = parse_american_odds(hold_match.group(1))
        hold_pct = int(hold_match.group(2))
        target_prob = implied_probability(target_odds)
        opponent_prob = 1 + (hold_pct / 100) - target_prob

        if not 0 < opponent_prob < 1:
            raise ValueError(f"Invalid hold leg '{token}'. Synthesized opponent probability must be between 0 and 1.")

        opponent_odds = american_from_probability(opponent_prob)
        fair_probs = devig([target_odds, opponent_odds], effective_devig_method)
        result = build_leg_result(target_odds, opponent_odds, fair_probs[0], fair_probs[1])
        return result, fair_probs[0]

    pair_match = PAIR_LEG_RE.fullmatch(token)
    if pair_match:
        first_odds = parse_american_odds(pair_match.group(1))
        second_odds = parse_american_odds(pair_match.group(2))
        fair_probs = devig([first_odds, second_odds], effective_devig_method)
        result = build_leg_result(first_odds, second_odds, fair_probs[0], fair_probs[1])
        return result, fair_probs[0]

    raise ValueError(f"Invalid leg format: {token}")

def fallback_devig_method(token: str) -> Union[DevigMethod, None]:
    if HOLD_LEG_RE.fullmatch(token):
        return DevigMethod.power
    if PAIR_LEG_RE.fullmatch(token):
        return DevigMethod.tko
    return None

def resolve_devig_method(
    per_call_devig_method: Union[DevigMethod, None],
    settings_devig_method: Union[DevigMethod, None],
    fallback_method: Union[DevigMethod, None],
) -> Union[DevigMethod, None]:
    return per_call_devig_method or settings_devig_method or fallback_method

def parse_legs(
    leg_odds_str: str,
    per_call_devig_method: Union[DevigMethod, None] = None,
    settings_devig_method: Union[DevigMethod, None] = None,
) -> Tuple[List[Dict[str, Union[int, float]]], List[float]]:
    leg_odds_str = substitute_avg(leg_odds_str)
    legs = [leg.strip() for leg in LEG_SPLIT_RE.split(leg_odds_str) if leg.strip()]
    if not legs:
        raise ValueError("Enter at least one leg.")

    results = []
    win_probs = []
    for leg in legs:
        effective_devig_method = resolve_devig_method(
            per_call_devig_method,
            settings_devig_method,
            fallback_devig_method(leg),
        )
        result, fair_prob = parse_leg(leg, effective_devig_method)
        results.append(result)
        win_probs.append(fair_prob)

    return results, win_probs

def parse_avg(avg_str):
    numbers = re.findall(r'-?\d+', avg_str)
    if not numbers:
        raise ValueError(f"Invalid avg format: {avg_str}")
    return int(sum(map(int, numbers)) / len(numbers))

def expected_value(win_probability: float, bet_odds: int) -> float:
    decimal_odds = american_to_decimal(bet_odds)
    return (win_probability * decimal_odds) - 1

def kelly_criterion(win_probability: float, bet_odds: int) -> float:
    decimal_odds = american_to_decimal(bet_odds)
    if decimal_odds == 1 or win_probability == 1:
        return 0
    return max(0, (win_probability * decimal_odds - 1) / (decimal_odds - 1))

def calculate_parlay_odds(odds_list: List[int]) -> int:
    decimal_odds = [american_to_decimal(odds) for odds in odds_list]
    parlay_decimal = 1
    for odds in decimal_odds:
        parlay_decimal *= odds
    return decimal_to_american(parlay_decimal)

def calculate_parlay_ev(win_probs: List[float], bet_odds: int) -> float:
    combined_prob = np.prod(win_probs)
    return expected_value(combined_prob, bet_odds)

def worst_case_devig(odds: List[int]) -> List[float]:
    probs = [implied_probability(odd) for odd in odds]
    total_prob = sum(probs)
    return [prob / total_prob for prob in probs]

def power_devig(odds: List[int], iterations: int = 100) -> List[float]:
    probs = [implied_probability(odd) for odd in odds]
    low = 0
    high = 1
    while sum(p**high for p in probs) > 1:
        high *= 2

    k = high
    for _ in range(iterations):
        k = (low + high) / 2
        total = sum(p**k for p in probs)
        if abs(total - 1) < 1e-10:
            break
        if total > 1:
            low = k
        else:
            high = k

    devigged = [p**k for p in probs]
    total = sum(devigged)
    return [prob / total for prob in devigged]

def probit_devig(odds: List[int]) -> List[float]:
    probs = [implied_probability(odd) for odd in odds]
    z_scores = stats.norm.ppf(probs)
    adjustment = np.mean(z_scores)
    adjusted_z_scores = z_scores - adjustment
    return stats.norm.cdf(adjusted_z_scores).tolist()

def tko_devig(odds: List[int]) -> List[float]:
    if len(odds) != 2:
        raise ValueError("TKO devigging method only works for two outcomes")
    p1, p2 = [implied_probability(odd) for odd in odds]
    q1, q2 = 1 - p1, 1 - p2
    b0 = np.log(p2 / q1) / np.log(p1 / q2)
    p = b0 / (1 + b0)
    return [p, 1 - p]

def goto_conversion(odds: List[Union[int, float]], total: float = 1, alpha: float = 1, beta: float = 1, eps: float = 1e-6) -> List[float]:
    decimal_odds = np.array([american_to_decimal(odd) if isinstance(odd, int) else odd for odd in odds])
    if len(decimal_odds) < 2:
        raise ValueError('len(odds) must be >= 2')
    if np.any(decimal_odds < 1):
        raise ValueError('All odds must be >= 1')
    probabilities = 1 / decimal_odds
    se = np.sqrt((probabilities - probabilities**2) / ((probabilities**alpha) / beta))
    step = (np.sum(probabilities) - total) / np.sum(se)
    output_probabilities = np.clip(probabilities - (se * step), eps, 1 - eps)
    return (output_probabilities / np.sum(output_probabilities)).tolist()

def devig(odds: List[int], method: DevigMethod = DevigMethod.wc) -> List[float]:
    devig_functions = {
        DevigMethod.wc: worst_case_devig,
        DevigMethod.power: power_devig,
        DevigMethod.probit: probit_devig,
        DevigMethod.tko: tko_devig,
        DevigMethod.goto: goto_conversion
    }
    try:
        result = devig_functions[method](odds)
        if abs(sum(result) - 1) > 1e-6:
            print(f"Warning: Devigged probabilities do not sum to 1 for method {method.name}. Sum: {sum(result)}")
        return result
    except Exception as e:
        print(f"Error in devigging with method {method.name}: {str(e)}")
        return worst_case_devig(odds)

def calculate_ev(win_prob: float, odds: int) -> float:
    return (win_prob * american_to_decimal(odds)) - 1

def format_odds(odds: Union[int, float]) -> str:
    return f"+{odds}" if odds > 0 else f"{odds}"

def format_ev(ev: float) -> str:
    return f"{ev:05.2f}%" if ev >= 0 else f"{ev:06.2f}%"

def create_embed(results: List[Dict[str, Union[int, float]]], ev: float, kelly: float, kelly_type: KellyType, wager_amount: float, combined_fair_odds: int, combined_win_prob: float, devig_method: DevigMethod, user_bankroll: float = None, is_parlay: bool = False, bet_odds: int = None) -> discord.Embed:
    embed = discord.Embed(color=EMBED_COLOR)
    
    if wager_amount is not None:
        embed.add_field(name=f"Wager Amount ({kelly_type.name})", value=f"```\n${wager_amount:.2f}{PADDING}\n```", inline=False)
    
    if ev is not None and kelly is not None:
        result_text = (
            f"EV: {format_ev(ev*100)}    {kelly_type.name}: {kelly:.2%}\n"
            f"FV: {format_odds(combined_fair_odds)}      WIN: {combined_win_prob:.2%}"
        )
        embed.add_field(name=f"Odds: {format_odds(bet_odds)}", value=f"```\n{result_text}\n```", inline=False)

    for i, result in enumerate(results):
        title = f"Leg {i+1}" if is_parlay else "Comparison"

        market_prob = result.get('market_prob', implied_probability(result['market_odds']))
        true_prob = result['win']
        market_prob2 = result.get('market_prob2', 1 - market_prob)
        true_prob2 = result.get('fair_prob2', 1 - true_prob)
        market_odds2 = result.get('market_odds2', american_from_probability(market_prob2))
        fair_odds2 = result.get('fair_odds2', american_from_probability(true_prob2))
        combined_odds = (
            f"Market Odds      Fair Odds\n"
            f"{market_prob*100:05.2f}%: {format_odds(result['market_odds']):>5}    {true_prob*100:05.2f}%: {format_odds(result['fair_odds']):>5}{PADDING}\n"
            f"{market_prob2*100:05.2f}%: {format_odds(market_odds2):>5}    "
            f"{true_prob2*100:05.2f}%: {format_odds(fair_odds2):>5}{PADDING}\n"
        )
        embed.add_field(name=title, value=f"```\n{combined_odds}\n```", inline=False)
    
    return embed

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_user_data(data):
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(data, f)

user_data = load_user_data()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        await bot.tree.sync()
        print("Command tree synced successfully")
        custom_activity = discord.Activity(name="powered by JOVEL", type=discord.ActivityType.custom)
        await bot.change_presence(activity=custom_activity)
    except Exception as e:
        print(f"Failed to sync command tree: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    parts = [part.strip() for part in message.content.split(':', 1)]

    if len(parts) == 2:
        try:
            bet_odds_str, leg_odds_str = parts
            bet_odds = parse_american_odds(bet_odds_str)
            user_id = str(message.author.id)
            user_settings = user_data.get(user_id, {})
            settings_devig_method = get_saved_devig_method(user_settings)
            kelly_type = KellyType[user_settings.get("kelly", "QK")]
            user_bankroll = user_settings.get("bankroll") if user_settings.get("bankroll_enabled", True) else None

            results, win_probs = parse_legs(leg_odds_str, settings_devig_method=settings_devig_method)
            combined_win_prob = np.prod(win_probs)
            combined_fair_odds = american_from_probability(combined_win_prob)

            ev = calculate_ev(combined_win_prob, bet_odds)
            kelly = kelly_criterion(combined_win_prob, bet_odds) * kelly_type.value
            wager_amount = kelly * user_bankroll if user_bankroll else None

            is_parlay = len(results) > 1
            embed = create_embed(results, ev, kelly, kelly_type, wager_amount, combined_fair_odds, combined_win_prob, settings_devig_method, user_bankroll, is_parlay, bet_odds)

            await message.channel.send(embed=embed)
        except Exception:
            await bot.process_commands(message)
            return

    await bot.process_commands(message)


@bot.tree.command(name='ev', description="EV Calculator")
@app_commands.describe(
    bet_odds='Enter the bet odds',
    leg_odds='Enter leg odds, pairs, or hold% legs (comma-separated)',
    kelly='Set Kelly Criterion type (FK, HK, QK, EK)',
    devig_method='Set devig method (wc, power, probit, tko, or goto)'
)
async def ev(interaction: discord.Interaction, bet_odds: int, leg_odds: str, kelly: str = None, devig_method: str = None):
    try:
        bet_odds = parse_american_odds(bet_odds)
        user_id = str(interaction.user.id)
        user_settings = user_data.get(user_id, {})
        
        per_call_devig_method = parse_devig_method(devig_method) if devig_method else None
        settings_devig_method = get_saved_devig_method(user_settings)
        
        if kelly:
            if kelly not in KellyType.__members__:
                await interaction.response.send_message(f"Invalid Kelly type: {kelly}. Valid options are: {', '.join(KellyType.__members__.keys())}", ephemeral=True)
                return
            kelly_type = KellyType[kelly]
        else:
            kelly_type = KellyType[user_settings.get("kelly", "QK")]
        
        user_bankroll = user_settings.get("bankroll") if user_settings.get("bankroll_enabled", True) else None

        results, win_probs = parse_legs(leg_odds, per_call_devig_method, settings_devig_method)
        combined_win_prob = np.prod(win_probs)
        combined_fair_odds = american_from_probability(combined_win_prob)

        ev = calculate_ev(combined_win_prob, bet_odds)
        kelly = kelly_criterion(combined_win_prob, bet_odds) * kelly_type.value
        wager_amount = kelly * user_bankroll if user_bankroll else None

        is_parlay = len(results) > 1
        effective_display_method = per_call_devig_method or settings_devig_method
        embed = create_embed(results, ev, kelly, kelly_type, wager_amount, combined_fair_odds, combined_win_prob, effective_display_method, user_bankroll, is_parlay, bet_odds)
        
        await interaction.response.send_message(embed=embed)

    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
    except Exception as e:
        error_message = f'An unexpected error occurred: {str(e)}'
        if interaction.response.is_done():
            await interaction.followup.send(error_message, ephemeral=True)
        else:
            await interaction.response.send_message(error_message, ephemeral=True)
        print(f"Unexpected error in ev command: {str(e)}")

@bot.tree.command(name='settings', description="Manage your calculator settings")
@app_commands.describe(
    bankroll='Set your bankroll amount',
    toggle_bankroll='Enable or disable bankroll calculations',
    kelly='Set Kelly Criterion type (FK, HK, QK, EK)',
    devig_method='Set devig method (wc, power, probit, tko, or goto)'
)
async def settings(interaction: discord.Interaction, bankroll: float = None, toggle_bankroll: bool = None, kelly: str = None, devig_method: str = None):
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = str(interaction.user.id)
        user_settings = user_data.get(user_id, {})

        if bankroll is not None:
            user_settings["bankroll"] = bankroll
        
        if toggle_bankroll is not None:
            user_settings["bankroll_enabled"] = toggle_bankroll

        if kelly is not None:
            if kelly in KellyType.__members__:
                user_settings["kelly"] = kelly
            else:
                await interaction.followup.send(f"Invalid Kelly type: {kelly}. Valid options are: {', '.join(KellyType.__members__.keys())}", ephemeral=True)
                return

        if devig_method is not None:
            if devig_method in DevigMethod.__members__:
                user_settings["devig_method"] = devig_method
            else:
                await interaction.followup.send(f"Invalid devig method: {devig_method}. Valid options are: {', '.join(DevigMethod.__members__.keys())}", ephemeral=True)
                return

        user_data[user_id] = user_settings
        save_user_data(user_data)

        response = "Settings updated:\n"
        if bankroll is not None:
            response += f"Bankroll set to ${bankroll:,.2f}\n"
        if toggle_bankroll is not None:
            response += f"Bankroll calculations {'enabled' if toggle_bankroll else 'disabled'}\n"
        if kelly is not None:
            response += f"Kelly Criterion type set to {kelly}\n"
        if devig_method is not None:
            response += f"Devigging method set to {devig_method}"

        await interaction.followup.send(response, ephemeral=True)

    except Exception as e:
        print(f"Error in settings command: {str(e)}")
        await interaction.followup.send(f"An error occurred while updating settings: {str(e)}", ephemeral=True)

if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_BOT_TOKEN'))
