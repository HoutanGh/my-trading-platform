#### V2 scaffolding
- [ ] Create `appsv2/` layout (`cli`, `core/{orders,risk,portfolio,strategies}`, `broker`, `events`, `logging`, `persistence`).
- [ ] Add a simple in-process event bus (publish/subscribe).
- [ ] Define core contracts: `OrderSpec` and ports for orders/market data/account.

#### Broker adapter
- [ ] Stub IB adapter implementing the ports; convert `OrderSpec` to IB orders; publish broker events.
- [ ] Wire basic event logging (append-only file/sqlite subscriber).

#### Core services
- [ ] OrderService (validate/size -> OrderSpec -> send to Order port).
- [ ] RiskManager (rules + checks on submit).
- [ ] PortfolioState (cache positions/orders/pnl from events).
- [ ] StrategyEngine shell using existing pure breakout logic.

#### CLI v2
- [ ] New CLI entrypoint that only sends intents to services and listens for status/events.

#### First working flow
- [ ] Bracket buy path end-to-end (CLI -> OrderService/Risk -> IB adapter -> events -> PortfolioState -> CLI feedback).

#### Next flows
- [ ] Breakout flow (bar stream -> breakout logic -> signal -> order).
- [ ] Cancel-all, account status, flatten.

#### Migration
- [ ] Keep v1 intact; add a runner for v2; port features over until v2 replaces v1.
