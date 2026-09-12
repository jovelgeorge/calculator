# devig & EV calculator

open-source discord bot that removes vigourish and calculates kelly & ev for event trading outcomes

## setup

1. clone the repository
2. create a virtual environment: `python3 -m venv env`
3. activate the virtual environment: `source env/bin/activate`
4. install requirements: `pip install -r requirements.txt`
5. create a `.env` file and add your Discord bot token:
   
```
DISCORD_BOT_TOKEN=your_token_here
```

6. run the bot: `discord.py`

## usage

the calculator can be used using the following syntax: `bet_odds:fair_odds` 

- two-way markets: `-130/110`
- multiple legs, separate by commas: `-130/110, -125/115`
- market average: `avg(-130, -145)/avg(110,115)`
- theoretical hold: `-130/4%`
- fractionals (kalshi make/take): `97.4c`

## under the hood

>converts odds to implied probability `p`
>`avg(...)` converts odds to fair(`p`), then averages
>for two outcomes, probit computes `z = (phi_inverse(p1) - phi_inverse(p2)) / 2`, then returns `phi(z)` and its complement (already complementary probabilities remain exact)

probit is our default devigging method. it is dependency-free and works brilliantly for overround and underround markets

>for theoretical hold `H`, total implied probability is `1 / (1 - H)`; the missing side is that total minus the supplied probability
> for independent parlays, fair probability is the product of leg probabilities
>for decimal payout `d`, EV is `p*d - 1`; full Kelly is `EV/(d-1)`

prediction-market prices use a fixed 100-contract model, ours specifically tailored to Kalshi:

> price `P` → purchase cost `100*P` and winning payout `$100`
> maker payout → `100 / purchase_cost`
> taker fee → `ceil_to_cent(0.07 * 100 * P * (1-P))`
> taker payout → `100 / (purchase_cost + fee)`
