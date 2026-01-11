- CLI receives input
- CLI calls core (OrderService)
- core validates the call, then publishes an event called "OrderIntent"
- core uses the OrderPort interface to send order 
- order is sent to IKBR adapter which talks to IKBR
- IBKR adapter publishes events for broker outcomes
- CLI is subscribed to events -> prints them




### Events
- one part of the program can announce something happened without directly calling another part 
- e.g core announced event and CLI registers call back 

- Events and EventBus interface live in core/
- in-process eventbus implementation lives in adapters/
- core + adapter publish events
- CLI subscribes and prints
- CLI wiring happenings in '__main__.py'

- 'events.py'
    - defines order related types
- 'bus.py' 
    - defines the EventBus interface
- 'ikbr_order_port.py'

### PnL Ingestion
#### core/pnl
- 'ports.py' defines the interfaces the core expects without caring about the CSV/DB
- 'service.py' orchestration entrypoint 
    - need to say if i want event publishing - ask what the benefits are


### Breakout
- reusable aspects
    - connecting to ikbr
    - subscribing to time data
- the only thing that is unique is the breakout strategy

#### what's left
- have a take proft and stop loss
    - if volatile larger stop loss
        - general volatility of stock (float)
        - and recent volatility that is the reason i am trading it
    - calculate take profit based the next highest price that has the most dense trades
        - to verify this, provide date so i can see if it is right
        - need to get historical data from this 

    - then i can test this
    - provide profit made
    - provide status of order 


---

## Learning

- dataclasses
- typing
- asyncio
- architecture
- event-driven design

- [ ] then go through the code and see if you understand