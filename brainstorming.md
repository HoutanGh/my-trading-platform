
#### BREAKOUT
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
- CLI evolution ideas (still one‑line first)

- buy AAPL qty=5 → market
- buy AAPL qty=5 limit=189.50 → limit
- Later: bracket AAPL qty=5 tp=1% sl=0.5%
- Then even later bracket AAPL qty=5 tp=calculate sl=calculate (different risks)
    - can see where it calculates by giving you price and date so that you can verify



- [ ] service has things that should be in orders, pretty sure service is not meant to have any ib_insync code
- [ ] could you not generalise breakout_watcher.py, maybe the pure logic has inputs that help the strings make sense in the function

#### CALENDAR
- see if i can run npm run dev and other stuff in cli


