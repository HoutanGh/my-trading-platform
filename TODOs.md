## GENERAL TODOs
- [ ] how to calculate breakouts for stocks
- [ ] understand how a backtesting framework would also work for this
- [ ] research more on investment analysis approach
- [ ] research on caclulating breakouts
- [ ] research on options pricing prediction
- [ ] same tp and sl functionality for normally buying
- [ ] research having multiple sessions open
- [ ] maybe have dry-runs
- [ ] for breakouts, when first take profit is taken the lowest SL should just be the lowest it can stay to stay green
- [ ] need kill switch
- [ ] how to calulate volume rotation and tie this into stock pairs trading
- [ ] backtesting start
    - [ ] get a plan going
- [ ] warm market detection
- [ ] scanner that takes into accout suddent volume and small percentage going up
1. fix breakout
2. understand breakout
3. calculate breakouts on my own
- [ ] check if i have trading permissions for stocks

### 13/02/26
- [ ] clean up TODOs
- [ ] test breakout

### FOCUSING ON BREAKOUT
- [ ] breakout gap: if orderfilled price is higher than tp1 you will have a loss
- [ ] stop loss got cancelled even though only one tp1 got taken


### 11and12/02/26
- [x] test breakout
- [ ] breakout debugging
    - [ ] tp
    - [ ] sl
    - [ ] trades cli command
    - [ ] check orders command
    - [ ] cleaning
    - detached and attached, keep attached for single take profit
    - Biggest unknown: exact IB behavior during partial parent fills in fast conditions; I’d treat that as a mode-specific TODO and lock first version to a clearly defined partial-fill policy.
    - [ ] understand order of this new method, where there are overlaps etc.
- [ ] clean TODOs
- [ ] what is this possitions thing at the start

### 10/02/26
- [x] fix breakout streaming failure defect
    - [x] check if there wont be conflict between the single recovery que and the global recovery pass
stop market doesnt not work outside_rth, stop suggesting this

how is the take profit set up? that seems to always trigger instantly and reliably
- [ ] understand take profit functionality - why that take profit was 100%
- [ ] make stop limits more reliable
- [x] decide on what to do first
    - trading session
    - breakout calculation
    - backtesting framework
    - pullback automation/buying
- [ ] understand how i could make this project public
    - [x] update README.md
    - [ ] general cleanup
    - [ ] what sensitive data i have currently
- [ ] when can-trading the error shows i cnt trade but the command says i can
- Det70StopLossFilled: RIME qty=100.0 price=1.25 expected=100 broker=100 - two stop losses were filled not 1
- rename to 7030
- error apps> Breakout watcher finished: breakout:RIME:1.25
apps> Value (Trade(contract=Stock(conId=759914622, symbol='RIME', exchange='SMART', primaryExchange='NASDAQ', currency='USD', localSymbol='RIME', tradingClass='SCM'), order=LimitOrder(orderId=705, clientId=1001, permId=1779274344, action='BUY', totalQuantity=100.0, lmtPrice=1.32, auxPrice=0.0, tif='DAY', orderRef='breakout:RIME:1.25', transmit=False, outsideRth=True, account='DUH631912'), orderStatus=OrderStatus(orderId=705, status='PreSubmitted', filled=0.0, remaining=100.0, avgFillPrice=0.0, permId=1779274344, parentId=0, lastFillPrice=0.0, clientId=1001, whyHeld='', mktCapPrice=0.0), fills=[Fill(contract=Stock(conId=759914622, symbol='RIME', exchange='SMART', primaryExchange='NASDAQ', currency='USD', localSymbol='RIME', tradingClass='SCM'), execution=Execution(execId='00025b49.698fb4f1.01.01', time=datetime.datetime(2026, 2, 12, 18, 31, 5, tzinfo=datetime.timezone.utc), acctNumber='DUH631912', exchange='NASDAQ', side='BOT', shares=100.0, price=1.32, permId=1779274344, clientId=1001, orderId=705, liquidation=0, cumQty=100.0, avgPrice=1.32, orderRef='breakout:RIME:1.25', evRule='', evMultiplier=0.0, modelCode='', lastLiquidity=2), commissionReport=CommissionReport(execId='', commission=0.0, currency='', realizedPNL=0.0, yield_=0.0, yieldRedemptionDate=0), time=datetime.datetime(2026, 2, 12, 18, 31, 5, 985214, tzinfo=datetime.timezone.utc))], log=[TradeLogEntry(time=datetime.datetime(2026, 2, 12, 18, 31, 5, 771654, tzinfo=datetime.timezone.utc), status='PendingSubmit', message='', errorCode=0), TradeLogEntry(time=datetime.datetime(2026, 2, 12, 18, 31, 5, 981265, tzinfo=datetime.timezone.utc), status='PreSubmitted', message='', errorCode=0), TradeLogEntry(time=datetime.datetime(2026, 2, 12, 18, 31, 5, 985214, tzinfo=datetime.timezone.utc), status='PreSubmitted', message='Fill 100.0@1.32', errorCode=0)], advancedError=''), Fill(contract=Stock(conId=759914622, symbol='RIME', exchange='SMART', primaryExchange='NASDAQ', currency='USD', localSymbol='RIME', tradingClass='SCM'), execution=Execution(execId='00025b49.698fb4f1.01.01', time=datetime.datetime(2026, 2, 12, 18, 31, 5, tzinfo=datetime.timezone.utc), acctNumber='DUH631912', exchange='NASDAQ', side='BOT', shares=100.0, price=1.32, permId=1779274344, clientId=1001, orderId=705, liquidation=0, cumQty=100.0, avgPrice=1.32, orderRef='breakout:RIME:1.25', evRule='', evMultiplier=0.0, modelCode='', lastLiquidity=2), commissionReport=CommissionReport(execId='', commission=0.0, currency='', realizedPNL=0.0, yield_=0.0, yieldRedemptionDate=0), time=datetime.datetime(2026, 2, 12, 18, 31, 5, 985214, tzinfo=datetime.timezone.utc))) caused exception for event Event<fillEvent, [[None, None, <function _attach_trade_handlers.<locals>.<lambda> at 0x7aadb23e5940>], [None, None, <function IBKROrderPort._submit_ladder_order_detached.<locals>.<lambda> at 0x7aadb23e6480>]]>
Traceback (most recent call last):
  File "/home/houtang/GitHub/my-trading-platform/.venv/lib/python3.11/site-packages/eventkit/event.py", line 202, in emit
    result = func(*args)
             ^^^^^^^^^^^
  File "/home/houtang/GitHub/my-trading-platform/apps/adapters/broker/ibkr_order_port.py", line 571, in <lambda>
    fill_event += lambda trade_obj, *_args: _submit_detached_exits_if_ready(trade_obj)
                                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/houtang/GitHub/my-trading-platform/apps/adapters/broker/ibkr_order_port.py", line 538, in _submit_detached_exits_if_ready
    manager = _LadderStopManager(
              ^^^^^^^^^^^^^^^^^^^
  File "/home/houtang/GitHub/my-trading-platform/apps/adapters/broker/ibkr_order_port.py", line 808, in __init__
    self._emit_protection_state_locked(state="protected", reason="initialized")
  File "/home/houtang/GitHub/my-trading-platform/apps/adapters/broker/ibkr_order_port.py", line 1177, in _emit_protection_state_locked
    stop_order_id=_trade_order_id(self._stop_trade),
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/houtang/GitHub/my-trading-platform/apps/adapters/broker/ibkr_order_port.py", line 662, in _trade_order_id
    return _maybe_int(getattr(getattr(trade, "order", None), "orderId", None))
           ^^^^^^^^^^
NameError: name '_maybe_int' is not defined

### 09/02/26
- did nothing

### 08/02/26

- [x] rename adapter/market_data files 
- [x] research how to the investment analysis
- [x] put take profit files into correct folders 
- [x] have a refresh (for new versions) option for the app instead of quitting 

### 06/02/26
- [x] optimise best tps and sls
- [x] cancel more than just one stock on breakout stop
- [x] breakout cancel instead of stop
- [x] breakout cancel ALL functionality
- [x] cli clear functionality 

### 05/02/26
- [x] brainstorm best tps and sls
- [x] do best tps and sls


### 04/02/26

- [x] being able to change orders 
- [x] workout how to do fast trades on cli - enter a session
- [ ] cli cleaning
    - [ ] short form
    - [ ] etc.

### 03/02/26
- [x] test streaming breakout - seemed to work need to investigate
- [x] need tps and sl in breakout status
- [x] remove the double apps> on cli
    - [x] investigate how to do cleaner cli
- [x] cache of runners
- [x] show config actually shows all the defaults configs
- [x] look at FATN breakout and see if there are any gaps


### 02/02/26
- [x] find the conversation regarding the timings - calculate breakout automation lag; review breakout_automation.md
- [x] streaming market data strategy

### 01/02/26
- [x] remove the columns
- [x] understand implementation of the streaming market data
- [x] change calendar prices into pounds

### 27/01/26
- [x] multiple take profits
- [x] make the stop losses all limit orders


### 26/01/26
- [x] second breakout strategy
- [x] see if u can connection to TWS live
- [x] need p and l from a single trade in cli or given in logs or trades for the day cli command
- [x] better logs


### 25/01/26
- [x] e2e calendar
- [x] TWS connection
- [x] Live connection


### 24/01/26
- [x] understand the e2e calendar
- [x] start it


### 23/01/26
- [x] make the default outside_rth = true
- [x] also need the ib gateway logs
- [x] improve positions
- [x] need to see what status of breakouts are 
- [x] work out exact strategy to use for the 1s time
    - what file structure looks like
- [x] get all the files that are needed for 
- [x] error: when i lose connection to broker

---

### BREAKOUT
- [x] somehow maintain watchers from before if cancelling
- [x] change the market type to LMT order and at the ask - for instant buy and need to see how long it takes (what is the delay) need it to be instant
- [x] latency investigation
- [ ] need to elaborate on this
    - Order submission robustness is thin: bracket children are placed even if order_id is still None after timeout; the runner stops right after submit without handling rejects, partial fills, or failed submissions. Improve by retrying or failing fast when no order_id, waiting for an accept/ack, and emitting a stop reason on failures.
    - Lifecycle and recovery are limited: single‑fire only, no re‑arm, no time/session windows, no reconnect/missed‑bar handling; non‑cancel exceptions bubble up and end the watcher without a structured stop reason. Improve with configurable schedules/timeouts and reconnect/resubscribe behavior plus explicit stop reasons.
- [x] need manual stop losses for out of hours breaks
- [ ] suggestions for tps (defaults)
- [ ] take into account strong volume (if it has really weak volume then dont do breakout)
- [ ] need to catch errors
    - [ ] IKBR not connected yet and trying buy/sell stuff etc. 
- [x] debug breakout
    - need positions from IKBR, verify with Trader Workstation
    - better information in logs 
- [x] need to properly understand the strategy to understand behaviour
    - does it start after mid tick is finished, or begins on the tick of the breakout
- just check if qty is actually retrieved or getting from how much ordered cos on trader work station was something like 79
- [x] cli option where u can just use all the values rather than including the keys
- [ ] need to be able to change tp and sl for orders
- [x] have tp and sl in the positions table
- [x] even tho qty is 0 on positions, they are still in table, understand why and then remove
- [x] IMPROVEMENT: if the price hits significantly higher than the bar then buy and not wait for next bar (but this probs needs 1s bars or something)
- [x] IMPROVEMENT: multiple tps at different percentages, maybe specified with dashes separating the prices, stop loss updates to the latest tp that got hit?
    - only need condition if outside RTH then need if statements and making sure they happen quickly as a replacement for stop losses - maybe this is preferred so level3 does not expose
    - understand effort  for 1s bars
- [ ] how branching works
    - how to run this while also developing it
- [ ] understand conditions
    - can i do more than one breakout watcher - yes
    - need notification of selling and buying somehow
    - just ask what things need to be added to make is clearer
    - breakout status - positions table already kinda has it
        - good to have tp and sl in the table
    - how does it work when i enter on the 
        
    - useful information in the table
- [x] get all the files related to the breakout automation
- [x] need to know p&l locally (not really tbf)
- [ ] get this cli on my phone
- [ ] default version that puts in closest value to the amount of money i want to put in for quick stuff
- [ ] need to be able to change everything about orders
    - levels, etc
    - cancelling


### CLI
- [x] CLI tabbing

### CALENDAR

- [x] running the front end stuff calendar in the cli
- [x] take an email from my gmail export the csv, add it to the table and etc. 

### Further Automation
- [ ] quick pullback automation
