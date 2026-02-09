### Micro/Macro/Flow
- Investment analysis
- options pricing to predict futures on stocks
- marco on a stock - does it have options
    - something that tells me about its future in terms of options

### Day trading session
- b just defaults to buying LMT 100
- tp attaches to the most recent one 
    - can tp 2.1 50 so only half gets attached
    - then again for another price
- sl same thing
- think about how to change 

- enter a session for stock
    - make sure breakouts are still running in background but dont output results onto cli until have exited the stock session?
    - limiting factor is that doesnt insta buy - shouldn't be too bad 
        - understand scenario where this is bad 
            - if somehow passes stop loss condition that if it lower than this price, close position
    - have different options 
    - be able to exit and re-enter session and doesnt cancel orders



### BREAKOUT
-  [ ] if market is closed use tps that are more conservative 

#### tp and sl
- 
- there seemed to be a delay for when the breakout happened and breakout assigning IDs and shit
- need to if changing code now affects current orders in app
- need to know the all the stages filled, partially filled etc.
- just need to know positions


### CLI 
- [ ] needs to autocomplete in app or maybe it never leaves the cli
### Ideas
- stop losses might not be good, research literally using if statements so that can use them in pre and post market
- also need to understand the exact logic of breakout tbh



#### Overall
- CLI Layer initialises the IKBR client and Service but then also calls code to build the orders, which then does separate calls
- need to think about the layers being completely separate (CLI Layer, IKBR (ib_insync layer), strategy layer?, frontend?)

- so implementing event bus MVP
    - its synchronous 
    - in-memory only 
    - and wont get live order stasuses unless adapter publishes them

### Event Bus
- asynchronous 

#### CLI
- CLI on the CL not on a new one, tab auto fills
- [ ] fix the cli flow (a lot of errors)
    - [ ] note down the flow
    - [ ] note down where there isn't error handling
    - [x] workout if there is better way to do or not (there is but maybe do the breakout first) - RPL?
- [ ] cli needs option to inform you if it is prepost, market or closed market
- [ ] have some information in the cli line
- [ ] short form and long form
- CLI evolution ideas (still one-line first)

- buy AAPL qty=5 → market
- buy AAPL qty=5 limit=189.50 → limit
- Later: bracket AAPL qty=5 tp=1% sl=0.5%
- Then even later bracket AAPL qty=5 tp=calculate sl=calculate (different risks)
    - can see where it calculates by giving you price and date so that you can verify



- [ ] service has things that should be in orders, pretty sure service is not meant to have any ib_insync code
- [ ] could you not generalise breakout_watcher.py, maybe the pure logic has inputs that help the strings make sense in the function

#### CALENDAR
- see if i can run npm run dev and other stuff in cli


## Pre-Trade Brief Brainstorm

### Your Thinking
- You want a simple CLI-first workflow: `brief TSLA`.
    - need to know what options are for this - marco, micro, flow - research on chatgpt
- The command should return a fast summary dashboard for decision support.
- Later, add more real-time guidance while trading - maybe on breakout
- You want it to reflect your intended trade levels (entry/stop), not only generic symbol stats.
- what if i enter a session for a stock that provides real time breakout details

### My Thinking
- Focus on non-obvious momentum/breakout behaviors:
  - follow-through vs fakeout - how far back do you have to go for this info
  - post-break hold/retest behavior and pullback depth
  - adverse-first risk (hits risk before reward)
  - market/time-of-day conditioning
  - execution friction
- With IBKR bars + top-of-book + Level 2, include microstructure:
  - understand how level 2 affects likelihood of breakout
  - near-touch imbalance
  - depth resilience/depletion
  - spread/depth shock
  - depth-adjusted slippage proxy
- Compress output into one screen:
  - tradeability score
  - execution mode (break-now / wait-close / wait-retest)
  - size multiplier
  - kill condition

### Proposed v1 Behavior
- `brief TSLA` uses saved TSLA plan levels if available.
- If no plan exists, infer candidate levels and label them as assumed.
- Allow overrides: `brief TSLA --entry X --stop Y`.
- Analytics only (paper-safe), no live order actions.

### Incremental Build Path
1. CLI summary using historical intraday bars.
2. Add optional L2 microstructure section.
3. Add `brief-live TSLA` for timed refresh updates.
