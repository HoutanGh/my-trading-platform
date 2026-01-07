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