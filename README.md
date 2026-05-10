# devig EV calculator

an open-source discord bot that calculates expected value (EV) and Kelly% for betting odds after removing the vig.

## setup

1. Clone the repository
2. Create a virtual environment: `python3 -m venv env`
3. Activate the virtual environment: `source env/bin/activate`
4. Install requirements: `pip install -r requirements.txt`
5. Create a `.env` file and add your Discord bot token:
   
```
DISCORD_BOT_TOKEN=your_token_here
```

6. Run the bot: `ev-calc.py`

## commands

Type `/ev` and it will show you a list of parameters:

- *bet_odds* — Enter the market odds
- *leg_odds* — Enter the two-way leg odds or fair odds value

Type `/settings` and it will show you a list of personal settings

- *toggle_bankroll* — Enable or disable bankroll calculations.
- *bankroll* — Set bankroll amount
- *kelly* — Set Kelly Criterion type: `HK`, `QK`, `EK`
- *devig_type* — Set devig method: `wc` (default), `pb` (probit), `tko` (TKO), `goto` (goto_conversion)

The following logics are supported:

- Two-way markets: `-130/110`
- Multiple legs: `-130/110, -125/115`
- Market averages: `avg(-130, -145)/avg(110,115)`
- Implied holds: `250/8%`

The calculator can also be toggled without the command tree for quick inline calculations using the syntax: `bet_odds:leg_odds` in any message.

- For calculating to a two-way market: `100:-135/125`
- For calculating to fair: `100:-130`
- For calculating parlays: `500:-130,-130,-130`

