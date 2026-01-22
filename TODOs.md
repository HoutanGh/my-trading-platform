### 23/01/26
- [ ] work out exact strategy to use for the 1s time
    - what file structure looks like
- [x] get all the files that are needed for 

- [ ] error: when i lose connection to broker
- [ ] need to see status of breakouts - if there are any
---

### BREAKOUT
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
- [ ] IMPROVEMENT: if the price hits significantly higher than the bar then buy and not wait for next bar (but this probs needs 1s bars or something)
- [ ] IMPROVEMENT: multiple tps at different percentages, maybe specified with dashes separating the prices, stop loss updates to the latest tp that got hit?
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
- [ ] get all the files related to the breakout automation
- [ ] need to know p&l locally (not really tbf)
- [ ] get this cli on my phone
- [ ] default version that puts in closest value to the amount of money i want to put in for quick stuff
- [ ] need to be able to change everything about orders
    - levels, etc
    - cancelling


### CLI
- [x] CLI tabbing

### CALENDAR

- [ ] running the front end stuff calendar in the cli
- [ ] take an email from my gmail export the csv, add it to the table and etc. 
