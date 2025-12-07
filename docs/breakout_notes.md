_breakout_menu

_breakout_live(service, client, watcher_tasks)
- gets symbol, level and qty from cli
- logs it
- runs_breakout_watcher 
    - subscribes to 1min data, sets the strategy - state = BreakoutState() (maybe call it strategy)
    - update_event = syncio.Event() - not sure what this does
    - _on_bars?