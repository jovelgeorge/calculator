# calculator

open-source discord calculator bot that removes vigourish and calculates kelly% & expected value for event trading outcomes

## setup

1. clone the repository
2. create a virtual environment: `python3 -m venv env`
3. activate the virtual environment: `source env/bin/activate`
4. install requirements: `pip install -r requirements.txt`
5. create a `.env` file and add your bot token:
   
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
- fractionals: `97.4c`

## under the hood

probit is my selected default devigging method. it is dependency-free and works great for overround and underround markets

>converts odds to implied probability `p`
>
>for two outcomes, probit computes `z = (phi_inverse(p1) - phi_inverse(p2)) / 2`, then returns `phi(z)` and its complement (already complementary probabilities remain exact)

this allows us to support:

> parlays, fair probability is the product of leg implied probabilities
>
>hold `H`, total implied probability is `1 / (1 - H)`; the missing side is that total minus the supplied probability
>
>decimal payout `d`, EV is `p*d - 1`; full Kelly is `EV/(d-1)`

prediction-markets use a fixed 100-contract model

> price `P` → purchase cost `100*P` and winning payout `$100`
> 
> maker payout → `100 / purchase_cost`
> 
> taker fee (kalshi) → `ceil_to_cent(0.07 * 100 * P * (1-P))`
> 
> taker fee (polymarket us) → `round_half_even_cent(0.0695 * 100 * P * (1-P))`
>
> taker fee (novig) → `round_half_up_5dp(c * 100 * P * (1-P))`
>   - c = 0.06 live straights (was 0.03 except NCAAF/MLB/NHL)
>   - c = 0.06 futures, every fill (golf/tennis futures exempt)
>
> taker payout → `100 / (purchase_cost + fee)`
