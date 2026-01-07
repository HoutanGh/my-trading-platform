#### Overall
- CLI Layer initialises the IKBR client and Service but then also calls code to build the orders, which then does separate calls
- need to think about the layers being completely separate (CLI Layer, IKBR (ib_insync layer), strategy layer?, frontend?)


#### CLI
- [ ] fix the cli flow (a lot of errors)
    - [ ] note down the flow
    - [ ] note down where there isn't error handling
    - [x] workout if there is better way to do or not (there is but maybe do the breakout first) - RPL?
- [ ] cli needs option to inform you if it is prepost, market or closed market
- [ ] have some information in the cli line
- [ ] short form and long form
- CLI evolution ideas (still one‑line first)

- buy AAPL qty=5 → market
- buy AAPL qty=5 limit=189.50 → limit
- Later: bracket AAPL qty=5 tp=1% sl=0.5%
- Then even later bracket AAPL qty=5 tp=calculate sl=calculate (different risks)
    - can see where it calculates by giving you price and date so that you can verify

#### BREAKOUT
- [ ] change breakout so the candle has to finish about the line and the next tick also
- [ ] test if it subscribes to 1min data, just test everything in general
- [ ] service has things that should be in orders, pretty sure service is not meant to have any ib_insync code
- [ ] could you not generalise breakout_watcher.py, maybe the pure logic has inputs that help the strings make sense in the function

#### CALENDAR
- [ ] note down the flow for the csv ingestion


