# Strategy Rules — Didi Index (Agulhada) & Bollinger "Fechou Fora, Fechou Dentro"

*Researched 2026-08-02 from Brazilian primary/secondary sources (list at bottom).*

# STRATEGY 1 — Didi Index / "Agulhada do Didi" (Odir "Didi" Aguiar)

## 1.1 Indicator construction

Base: three **simple moving averages of the close**: SMA(3), SMA(8), SMA(20). (One minority source uses 21 for the slow average; 20 is the standard in Nelogica/Tryd/MT5 implementations.)

The **Didi Index** normalizes the three SMAs by **dividing each by the 8-period SMA** (per tradergrafico.com.br, matching Nelogica/Tryd implementations):

```
curta  = SMA(close, 3)  / SMA(close, 8)      // "fast" line
media  = SMA(close, 8)  / SMA(close, 8) = 1  // flat baseline
longa  = SMA(close, 20) / SMA(close, 8)      // "slow" line
```

Display convention varies: some platforms plot the ratio around **1.0**, others subtract 1 (or plot percent displacement) so the baseline sits at **0**. Mathematically equivalent. The fast and slow lines oscillate around the fixed middle line.

## 1.2 The "agulhada" (needling) signal

- Chart-space definition (Didi's original phrasing): **all three SMAs pass simultaneously through the real body of the same candle**, converge to (nearly) one point, then separate — "like threads passing through the eye of a needle."
- Indicator-space definition: the fast (curta) and slow (longa) lines **cross the baseline at (nearly) the same bar, in opposite directions**.

**Buy (agulhada de alta):** after the convergence, the averages exit ordered **3 above, 8 in the middle, 20 below** (on the index: curta crosses UP through baseline while longa crosses DOWN through it).
**Sell (agulhada de baixa):** exact mirror — 20 above, 8 middle, 3 below.

Critical detail (tradergrafico): the **8-period must exit between the other two**; and per Didi's own teaching (agulhadadodidi.blogspot), near-misses count: "se após colocar uma lupa, você perceber que não foi uma agulhada por um triz, considere-a" (if it missed being an agulhada by a hair, count it). Didi jokingly calls the tolerance question the "agulhada Queijo Minas" — crossings rarely happen at exactly one point, so detectors use a proximity threshold to the middle average (DIDI ALERT/mql5 exposes this as a 3-level sensitivity setting).

**Entry timing:** Didi's blog states entry "na abertura da segunda barra seguinte ao cruzamento" (open of the second bar after the cross) — i.e., wait one confirmation candle after the crossing bar. Tradergrafico likewise: wait one additional candle.

Pseudocode:

```
tol = epsilon  // proximity tolerance ("Queijo Minas" sensitivity)
agulhada_alta  = crossover(curta, media_baseline) AND crossunder(longa, media_baseline)
                 within same bar (or |curta_cross_bar - longa_cross_bar| <= tol_bars)
                 AND order after cross: curta > media > longa
agulhada_baixa = mirror
BUY  at open of bar (cross_bar + 2) if agulhada_alta AND confirmations OK
SELL at open of bar (cross_bar + 2) if agulhada_baixa AND confirmations OK
```

## 1.3 Confirmation stack (Didi's 5-indicator system)

Per TradingView "Didi Index Plus", traderdicas.com and the DIDI ALERT manual (mql5), Didi's full system = **Didi Index + DMI/ADX + Bollinger Bands + TRIX + Stochastic**:

| Indicator | Typical params | Rule in Didi's method |
|---|---|---|
| DMI/ADX | **period 8** (traderdicas) | Trend confirmed when **ADX is RISING and above level 32** (DIDI ALERT default 32, adjustable 10–45). No trend when ADX is below both DI+ and DI−, or ADX falling with value ≤ 32. DI+>DI− for buys, DI−>DI+ for sells. |
| "ADX chutado" (kicked ADX) | — | A sharp reversal of the ADX line's direction ("kick"). Didi's blog: when ADX reverses direction with a kick it flags potential tops/bottoms → exhaustion warning / exit signal, not entry. |
| Bollinger Bands | 20, 2 dev is the author-preferred setting; **community split — some (incl. DIDI ALERT) use period 8, dev 2** | Bands must be **opening (expanding)** at/after the agulhada = timing to enter; **bands starting to close = movement ending → exit**. |
| Stochastic | slow **8, 3, 3** | Direction agreement; used with TRIX for exits and to avoid signals in trendless periods. |
| TRIX | **9** (some configs add 3-period smoothing) | Direction agreement; TRIX+Stochastic both turning against position = exit (TradingView Didi Index Plus: "TRIX is selling and Stochastic just gave the sell signal, or vice-versa → exit"). |

## 1.4 Quality classifications and named variants

- **Agulhada perfeita:** the averages, coming from opposite extremes, cross **exactly at the same point on the baseline**. Per Didi himself, "quase impossível" to find; the higher the timeframe (daily/weekly/monthly), the more powerful.
- **"Agulhada completa"** (DIDI ALERT terminology): agulhada + "SIM" for ADX trend (rising, >32) + "SIM" for Bollinger opening — the highest-grade signal.
- **Simple alert:** agulhada without ADX/Bollinger confirmation — lower grade.
- **Ponto falso (false point):** the **long average crosses the intermediate one while the short average is moving in the opposite direction** (Didi's folkloric phrasing on blog.agulhada.com: the "boi amarelo pula a cerca" while the "boi azul" runs the other way). It is a deceptive signal — often means take the opposite trade.
- (Related Didi vocabulary exists — e.g., "ponto contínuo" — but no reliable public rule definition was found; flagged as a gap.)

## 1.5 Intraday usage in Brazil

- Method is timeframe-agnostic ("todos os tempos gráficos, todos os ativos"); Brazilian day traders apply it on **5m and 15m candles on WIN (mini-index) and WDO (mini-dollar)** with the same parameters (3/8/20; ADX 8 >32; Stoch 8,3,3; TRIX 9).
- Consensus caveat repeated across sources (TC, DIDI ALERT): **lower timeframes produce more false agulhadas**; the higher the timeframe, the more assertive. DIDI ALERT recommends monitoring 3 ascending timeframes (M5→D1 range) simultaneously.
- Some intraday variants replace TRIX with MACD as directional filter (daytradedehoje).

---

# STRATEGY 2 — Bollinger "Fechou Fora, Fechou Dentro" (FFFD)

Taught by Alexandre Wolwacz ("Stormer") — it is **Setup 103** in Leandro & Stormer's *Manual de Setups Vol. 4 (Setups Baseados na Banda de Bollinger)*.

## 2.1 Parameters

- Bollinger Bands: **20-period arithmetic mean, ±2 standard deviations**. Fábrica de Setups specifies the mean on **typical price** ("média aritmética de 20 períodos do preço típico"); most retail implementations use close. No Brazilian-specific parameter variant found — 20/2.0 is standard.

## 2.2 Core rules (long side; short is symmetric)

1. **Fechou fora:** a candle **CLOSES below the lower band**.
2. **Fechou dentro:** a subsequent candle **CLOSES back above the lower band** (inside the bands).
3. **Entry trigger (Stormer):** buy stop at the **break of the HIGH of the candle that closed inside** — executed by the next candle "ou no máximo pelo candle subsequente" (at most the one after that). Fábrica de Setups adds a "margem de disparo" (tick offset) above the trigger price. A simpler variant enters at market on the close of the inside candle.
4. **Stop-loss:** below the **LOW of the candle that closed outside** the band (Stormer). Variant: the lowest low of the two candles (outside + inside) — effectively the same level in most cases.
5. **Targets** — three documented conventions:
   - **Stormer/Portal do Trader partial-exit convention:** sell **half at the close of the candle that touches the central band (20-SMA)**, the other half **at the close of the candle that touches the opposite band**. Variant: 70% at the central band.
   - **Opposite band or central band** as single full target.
   - **Fixed ratio (Fábrica de Setups):** stop size = range of the trigger candle; target = **1×, 1.6× or 2× the stop**.

Pseudocode (long):

```
BB = Bollinger(20, 2.0)
state IDLE:
  if close[t] < BB.lower[t]: state = OUTSIDE (mark low_outside = low[t], keep updating while outside)
state OUTSIDE:
  if close[t] > BB.lower[t]:            // closed back inside
      trigger  = high[t] + tick_margin
      stop     = min(low_outside, low[t])
      validity = 2 candles               // next candle or the one after
      state = ARMED
state ARMED:
  if high[t] > trigger within validity: ENTER LONG at trigger
      target1 = middle band touch (exit 50–70%)
      target2 = upper band touch (exit rest)   // or target = k * (entry - stop), k in {1, 1.6, 2}
  else after validity: signal CANCELLED, state = IDLE
```

## 2.3 Candles allowed between "outside" and "inside" — ambiguity

Sources disagree:
- **Strict/canonical form** (Fábrica de Setups, TC, Invest Academy): exactly **two consecutive candles** — one closes outside, the **very next** closes inside.
- **Loose form** (ForceSystem indicator, several robot implementations): **one or more candles may close outside**; the signal fires on the **first** candle that closes back inside. No source gives a hard cap of N outside candles, but the practical warning is that many consecutive closes outside = "walking the bands" = strong trend, don't fade.
- **Trigger invalidation** is better defined: per the Stormer-derived description, the break of the inside candle's high must occur by the **next candle or at most the subsequent one** (~2 candles), otherwise the setup is dead.

## 2.4 Mean-reversion vs trend variant

- FFFD proper is explicitly a **counter-trend / mean-reversion (fade) setup** ("operação contra a tendência").
- The **continuation case is treated as a separate concept, not a variant of FFFD**: a close outside the band that is NOT followed by a close back inside is read as **trend-continuation** ("fechou fora e continuou" = "surfing/walking the bands"); Stormer's manual covers it as separate setups (Setup 101 "Walking up the bands", Setup 102 "Compra na banda inferior"). TC/Invest Academy state flatly: closing outside the bands is a continuation signal; only the close back inside converts it into a reversal signal.
- A quant variant (QuantBrasil "Reversão das Bandas de Bollinger") inverts the trigger: **enter long at the close of the candle that closes below the lower band** (previous candle having closed inside), exit via **time stop (close of N candles ahead, e.g., 2)** and **percentage stop (e.g., 2%)** — useful as a backtestable cousin, but it is not the classic FFFD.

## 2.5 Intraday usage in Brazil

- Widely used on **WIN/WDO at 5m and 15m**, and on daily charts for swing (Stormer's original context is swing/position on stocks).
- Portal do Trader claims hit rates "above 80%" on liquid stocks like PETR4/VALE3 — a marketing claim, unverified.
- Repeated practitioner warnings: only fade when the market is **range-bound / bands open**; in a strong trend price walks the band and FFFD signals fail ("reverter contra isso é entrar na frente de um trem" — Trader Brasil). Mid-session low-volatility periods (band squeeze around lunchtime) are noted as a poor time for fades but a setup for afternoon breakouts.

---

## Key ambiguities / disagreements (both strategies)

These are prime candidates for backtest sweep dimensions rather than fixed choices:

1. **Didi slow average: 20 vs 21 periods** — 20 dominates (Nelogica, Tryd, MT5, tradergrafico); one TradingView source says 21.
2. **Didi Bollinger period: 20 vs 8** — genuinely split in the Didi community; traderdicas notes the confusion explicitly, DIDI ALERT ships with 8/2.
3. **Didi Index display: ratio around 1.0 vs displacement around 0** — platform cosmetic difference, same math (divide by SMA8).
4. **Agulhada tolerance** — no canonical epsilon; Didi says count near-misses; detectors expose sensitivity settings.
5. **"ADX chutado"** — consistently described as a sharp directional reversal ("kick") of the ADX line marking exhaustion, but no source gives a quantitative definition (e.g., minimum slope change). The quantitative rule that IS consistent: **ADX rising above 32 = trend confirmed**.
6. **FFFD outside-candle count** — strict (exactly 1) vs loose (≥1, signal on first close back inside); no consensus.
7. **FFFD targets** — partial at middle band + rest at opposite band (Stormer) vs fixed 1/1.6/2 R multiples (Fábrica de Setups); both are legitimate documented conventions.
8. Original primary texts (Stormer's Manual de Setups Vol. 4 full text; Didi's paid course) are paywalled; the rules above are cross-checked across multiple independent secondary sources.

Sources: [Nelogica – Didi Index](https://ajuda.nelogica.com.br/hc/pt-br/articles/13161968774299-Didi-Index), [Trader Gráfico – Didi Index](https://tradergrafico.com.br/blog/?id=22), [mql5 – DIDI ALERT manual (PT-BR)](https://www.mql5.com/pt/blogs/post/755751), [Agulhada do Didi blogspot – APRENDA](https://agulhadadodidi.blogspot.com/p/aprenda.html), [blog.agulhada.com – Ponto Falso](https://blog.agulhada.com/ponto-falso/), [TradingView – Didi Index Plus](https://www.tradingview.com/script/el7AaNVM-Didi-Index-Plus/), [TraderDicas – configuração do setup do Didi](https://traderdicas.com/completo-como-configurar-o-setup-do-didi-passo-a-passo/), [Memórias de um Trader – Aula 08 Agulhada do Didi](http://memoriasdeumtrader.blogspot.com/2009/09/aula-08-agulhada-do-didi.html), [Investing.com Brasil – A Agulhada do Didi](https://br.investing.com/analysis/a-agulhada-do-didi-5373), [TC – Bandas de Bollinger](https://site.tc.com.br/blog/renda-variavel/bandas-de-bollinger-estrategia), [Fábrica de Setups – RM001 Bollinger FFFD](https://fabricadesetups.com.br/2023/05/28/bollinger-fffd/), [Portal do Trader – setups com Bandas de Bollinger e FFFD](https://portaldotrader.com.br/plano-tnt/analise-tecnica-na-pratica/setups-com-indicadores-simples/operando-setups-com-bandas-de-bollinger-e-fffd), [L&S – Manual de Setups Vol. 4](https://ls.com.vc/livro/manual-de-setups-volume-4-setups-baseados-na-banda-de-bollinger-ebook), [QuantBrasil – Reversão das Bandas de Bollinger](https://quantbrasil.com.br/estrategias/reversao-das-bandas-de-bollinger-de-compra/), [Trader Brasil – Bollinger no Day Trade](https://www.traderbrasil.com/blog/bandas-de-bollinger-estrategias-day-trade.php), [ForceSystem – indicador FFFD](https://www.forcesystem.com.br/indicador-de-cor-fechou-fora-fechou-dentro-bandas-de-bollinger/), [Invest Academy – Bandas de Bollinger](https://portal.invest.academy/bandas-de-bollinger-conheca-esse-importante-indicador/)
