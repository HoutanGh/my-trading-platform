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
    
