# EV calculator

Discord worker for two-outcome probit devigging, independent parlays, expected value, signed Kelly analysis, and prediction-market prices. Calculations use complete chat messages. `/settings` selects the Kelly fraction; `/ev` and devig-method selection have been removed.

## Code layout

- `calculator.py`: parsing and all calculation logic, including prediction-market fees.
- `discord.py`: Discord responses, formatting, preferences, and startup.

Run `discord.py` as a script. Its small import bootstrap resolves the installed Discord library despite the shared name; application tests load this file as `calculator_discord`.

## Inputs

| Message | Result |
| --- | --- |
| `-250/180` | Original and fair probabilities/odds for both outcomes |
| `-198:-250/180` | Compare offered −198 against the first outcome's devigged probability |
| `100:-130` | Compare offered +100 against already-fair −130 |
| `-130/4%` | Infer the opposite market quote from 4% theoretical hold, then devig both sides |
| `47c` | Maker and taker effective odds and fee for 100 contracts |
| `97.4c` | Fractional-cent prediction-market price |
| `40c:134` | Maker/taker EV and Kelly against already-fair +134 |
| `40c:-250/180` | Maker/taker comparison against a two-sided reference market |
| `40c:-130/4%` | Maker/taker comparison against an estimated hold reference |
| `300:-250/180,-130/4%,120` | Compare against a mixed independent parlay |
| `-250/180,-130/4%` | Per-leg tables and combined fair probability/odds |
| `100:avg(-130,-140)/avg(110,120)` | Average each side's implied probabilities before devigging |

The colon separates the offered price from reference legs. Commas outside `avg(...)` separate legs. Each leg's first outcome is selected. American odds are integers with magnitude at least 100; a positive sign is optional. A plain reference is already fair. `avg(...)` averages probabilities, not American odds; one item is allowed, nesting is not. `AVG`, `C`, `¢`, Unicode minus, fractional holds, and surrounding whitespace are accepted.

Prices range from 1c through 99c inclusive, with up to two decimal places in cents (for example `97.41c`). Whitespace between the number and cent suffix is rejected. A lone American number or average is silent. Standalone lists such as `100,200` produce a parlay. Colon expressions such as `110:105` are valid calculations, so bare score-like messages can trigger the bot. Extra prose, internal newlines, empty/trailing legs, nested averages, three-way markets, and invalid prices/probabilities are silently ignored. Limits are 2,000 input characters and 20 legs; results exceeding Discord embed limits are also silent.

## Calculations

American odds convert to implied probability `p`. For two outcomes, probit computes `z = (Phi_inverse(p1) - Phi_inverse(p2)) / 2`, then fair probabilities `Phi(z)` and its complement. Already complementary probabilities stay exact. This is the sole devig method, including underround markets. Python's standard-library normal inverse and complementary error function avoid NumPy/SciPy dependencies.

For theoretical hold `H`, the sum of market probabilities is `1 / (1 - H)`. The inferred opposite probability is that sum minus the supplied side. Both market probabilities must be strictly between zero and one, with `0 <= H < 1`. Thus −110/−110 represents **4.54545% theoretical hold**, versus 4.76190% overround. No arbitrary hold cap is imposed: `-200/30%` is mathematically valid and estimates a 44.40% fair probability; `+2500/4%` is impossible and silent. These are model estimates, not a recovered real market quote.

Independent parlay probability is the product of leg probabilities. For correlated outcomes, supply a whole-bet fair quote instead. With decimal payout `d`, EV is `p*d - 1`; full Kelly is `EV/(d-1)`. Negative Kelly remains visible to identify negative-EV bets. Preferences scale this by FK=1, HK=1/2, QK=1/4 (default), or EK=1/8. Original probabilities, exact prices, and fees feed calculations directly. Only displayed American odds are rounded to the nearest integer; percentages and money display two decimals. Even money displays +100.

### Prediction-market fee model

Version 1 matches the selected reference model: **100 contracts, no maker fee, and taker fee `ceil_to_cent(0.07 * 100 * P * (1-P))`**. Each contract pays $1 on a win. Maker decimal payout is `100 / purchase_cost`; taker decimal payout is `100 / (purchase_cost + fee)`, using dollar costs for all 100 contracts. Fee arithmetic uses exact integers at 0.01-cent price precision, avoiding floating-point ceiling errors.

The Kalshi-labeled output is this fixed reference quote, not a claim that all venues, markets, or accounts share this fee schedule. Venue selection, maker fees, rebates, and live fee lookup are outside this version. At 47c the fee is $1.75 for 100 contracts; a single contract would instead round to a 2c fee.

Discord embeds use compact `Odds:`, `Kalshi:`, and `Fair Odds:` headers. Labels stay white while calculated values use bright-yellow ANSI highlighting. Columns expand to fit wide odds, and explanatory footers are omitted.

## Run

Use Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Set `DISCORD_BOT_TOKEN` in the environment or an untracked `.env`, then run:

```sh
.venv/bin/python discord.py
```

Enable Message Content Intent for the Discord application. The bot needs View Channel, Send Messages (Send Messages in Threads for threads), and Embed Links. Read Message History enables replies; without it the bot sends a regular channel message. Responses suppress mentions and ignore bots/webhooks. A rejected send is logged without retrying a duplicate.

`/settings` accepts only `kelly`. Existing valid Kelly choices are retained; obsolete bankroll and devig fields disappear on the next successful atomic settings save. `USER_DATA_FILE` optionally sets the settings JSON path; default is `user_data.json` in the working directory. Keep this file private. Invalid settings files fail startup rather than being silently overwritten.

## Deployment status and rollout

DigitalOcean tracks the repository's `testing` branch and runs the worker with `python discord.py`. Startup synchronizes global commands, removing obsolete command options for the application whose token is used. The local Kelly settings file may be replaced during a deployment; durable cross-deployment persistence would require external storage. Optional `APP_REVISION` is printed alongside the `probit-pm-v1` startup marker.

The `testing` branch retains the offline test suite and verification notes. Release code is verified there before promotion to `master`.
