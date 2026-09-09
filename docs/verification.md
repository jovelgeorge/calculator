# Verification

Offline verification uses Python 3.12 and mocked Discord messages; it does not send messages or validate production credentials/deployment.

| Input | Expected output |
| --- | --- |
| `-250/180` | Fair 67.94% / 32.06%, −212 / +212 |
| `-198:-250/180` | EV 2.25%, QK 1.12% |
| `100:-130/110` | EV 8.91%, QK 2.23% |
| `-130/4%` | Fair probability 54.44451797588348%, displayed −120 |
| `47c` | Maker +113, taker +105, fee $1.75 |
| `40c:134` | Maker EV 6.84%, QK 1.14%; taker EV 2.53%, QK 0.45%, fee $1.68 |

Tests compare all 9,801 supported price points against an independent Decimal ceiling oracle. They also cover invalid chat inputs, mixed legs, averaging, hold inference, symmetry, exact complementary probabilities, negative-EV Kelly, large displayed odds, settings migration/save failure, reply fallback, and command registration.

## Historical comparison

Executed the original `remove_vig_two_way` and `calculate_ev` functions extracted via AST from both `9917467` (inspected production build) and `0fc244a` (branch baseline), without loading tokens or connecting a bot. Both return:

- `-250/180`: proportional fair probability 66.6666666667%, displayed −200/+200.
- `100:-130/110`: proportional probability 54.2743538767%, EV **8.5487077535%**. The historical chat handler uses that probability for EV. The earlier claim that it returned 9.09% was incorrect for this path.

Three intentional changes must be distinguished:

1. **Devig method:** probit changes those probabilities to 67.9402123747% and 54.4574000151%, respectively. The second therefore has 8.9148000302% EV.
2. **Display:** old conversion truncated and rounded American odds to increments of five or ten. New display rounds to the nearest integer and never feeds those strings back into calculations.
3. **Parsing:** standalone devig, mixed reference legs, hold expressions, and cent offers now have a bounded complete-expression parser. A valid existing colon comparison remains supported, while malformed input becomes silent rather than raising or responding with errors.

The screenshot's `40c:134` taker EV 2.56% / QK 0.46% corresponds to using rounded +140 odds downstream. The implementation uses the exact $41.68 total cost and therefore returns 2.53% / 0.45% while still displaying +140. This is deliberate.

This is a focused mathematical baseline comparison, not a claim that every behavior on current master has been exercised. Live Discord appearance, permissions, and the eventual DigitalOcean deployment remain rollout checks.
