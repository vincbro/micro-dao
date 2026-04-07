# System Architecture: The MILP Optimization Engine

[Watch demo](https://youtu.be/A1W0hbRz9kY)

The standard approach to Energy Management Systems (EMS) is a **Greedy Algorithm**. A greedy system looks at a dataset of Day-Ahead electricity prices, selects the cheapest 15-minute discrete blocks, and schedules production. 

While computationally cheap, greedy algorithms suffer from tunnel vision: they optimize strictly for **OPEX** (energy cost) and completely ignore the cost of **state mutations**. 

In physical systems, changing state (a "Cold Start" of the electrolyzer) incurs a heavy **CAPEX** penalty via hardware degradation. A greedy algorithm might flip the system state ON and OFF 10 times a day to chase micro-fluctuations in power prices, ultimately destroying multi-million SEK hardware to save a few pennies in electricity.

**micro-dao solves this by using Mixed-Integer Linear Programming (MILP) to find the absolute global optimum, balancing OPEX savings against CAPEX state-mutation penalties while respecting physical storage limits.**

### The Objective Function
Instead of routing blindly based on price, the `pulp` MILP solver treats the electrolyzer as a stateful machine and minimizes a unified cost function:
> **Minimize:** `(Energy Price * Power * Time) + (CAPEX Penalty * State Transition Events)`

To enforce physical and logical reality, the algorithm applies a strict set of mathematical constraints:

#### 1. Dynamic Constraint Generation (Uptime/Downtime)
We do not hardcode static configuration values (e.g., `MIN_UPTIME = 2h`). Instead, the optimizer generates these bounds dynamically at runtime based on the system's current state. 
- It evaluates market volatility (the spread between average and minimum prices).
- It pulls the real-time financial penalty of a startup event from the Digital Twin.
- **`Dynamic Minimum Uptime = CAPEX Penalty / Hourly OPEX Savings`**
- *Result:* The algorithm self-adjusts. On days with flat pricing, it stretches the minimum uptime constraint to force continuous baseload operation. On highly volatile days, it shrinks the constraint, allowing the system to rapidly capitalize on severe price drops.

#### 2. Flow-Based Storage & Demand Constraints
The system does not optimize for a naive, static daily production target. It operates as a true continuous-flow digital twin. 
- **The Bathtub Model:** The solver tracks a continuous variable `s[i]` representing the physical kilograms of hydrogen in the storage tanks. 
- For every 15-minute interval, it must satisfy the balance equation: `Storage = Previous Storage + Production - Interval Demand`.
- **Terminal Constraints:** The solver is mathematically banned from exceeding the physical tank capacity, dipping below 0, or "cheating" by simply draining the tank to save OPEX. A terminal constraint ensures the tank finishes the day with at least the amount of hydrogen it started with.

#### 3. Sub-Interval Precision
The power grid exposes data in rigid 15-minute discrete blocks, but physical systems require continuous precision.
- The MILP model solves this using a hybrid variable space: it pairs a binary state variable (`y` for ON/OFF) with a continuous fractional variable (`x` for active duration).
- This allows the solver to run at 100% capacity for a fraction of an interval (e.g., 9 minutes of a 15-minute block) and schedule a hard `SIGSTOP` the exact millisecond the storage quota is satisfied, preventing wasted OPEX.

#### 4. Constraint Hardening (The "Ghost-Run" Loophole)
Mathematical solvers are inherently "lazy" and will exploit any unbound edge cases. If instructed to maintain an "ON" state for 4 hours to avoid a shutdown penalty, the solver might attempt to run at a 0.01% fractional load, technically satisfying the state requirement without paying for electricity.
- We harden the model against this using a strict adjacency constraint: `x[i] >= y[i] + y[i + 1] - 1`.
- *Translation:* If the system claims to be continuously ON across multiple intervals, it **must** run at exactly 100% capacity and pay the full market rate. Fractional loads are strictly bounded to the terminal interval immediately preceding a state shutdown.

### The Outcome: Global vs. Local Optima
By feeding raw market snapshots and real-time state data into the MILP solver, `micro-dao` completely avoids the pitfalls of local optima. The system will frequently choose to ignore a localized 15-minute dip in electricity prices if capturing it requires a state transition. Instead, it shifts the entire production block to a slightly more expensive, but completely contiguous time window, radically reducing unnecessary wear cycles while hitting precise production targets.

### Future Improvements

If I had more time to expand the `DigitalTwin` simulation, I would look into bridging the gap between our current financial-state model and true physical electrochemistry:

- **Continuous Thermal Decay:** Right now, the penalty for starting up is static and based entirely on overall stack health. I'd like to implement a thermal mass model that tracks how long the system has been offline. This would let the MILP solver differentiate between a low-penalty "warm start" (restarting after 30 minutes) and a high-penalty "cold start" (restarting after 12 hours).
- **Non-Linear Efficiency (Polarization Curves):** The current solver assumes flat, linear production efficiency. Because real electrolyzers operate on a curve and lose efficiency at maximum load, I'd like to use piecewise linear approximation to let the solver hunt for the thermodynamic "sweet spot" (e.g., running at 65% capacity for a longer duration to maximize hydrogen yield).
- **Component-Specific Wear:** Instead of a single global health percentage, I'd like to split degradation into specific vectors (like membrane thinning vs. catalyst loss). The optimizer could then dynamically generate constraints to protect the hardware's weakest link at any given time.

## Running the Simulation

This project uses `uv` for dependency management. To execute the standard multi-day benchmark against the provided market data, run:

```bash
uv run src/main.py data/*
# OR
./run.sh
```

### What happens during execution?
When you trigger the simulation, the engine performs a direct 3-way benchmark to demonstrate the financial impact of the MILP model against standard industry logic, as well as benchmarking the underlying math engine itself.

1. **[1] GREEDY EMS (Baseline):** Simulates a standard sorting algorithm that blindly chases cheap electricity, serving as our financial baseline.
2. **[2] MILP (Heuristics ON):** The production-grade solver. Uses mathematical "guessing" (heuristics) to rapidly find the optimal integer paths, generally solving in ~1-5 seconds.
3. **[3] MILP (Heuristics OFF):** Forces the CBC solver to ignore shortcuts and use pure Branch-and-Bound math to prove global optimality. Used to ensure the heuristics aren't trapping the system in a sub-optimal mathematical branch.

The script tracks all 3 isolated `DigitalTwins` across the dataset and outputs a Terminal UI detailing the exact **OPEX**, **CAPEX**, **Avoided Cycles**, and **Solve Times** for each strategy.

### Simulating Degradation Behavior (The `--health` Flag)

The true power of the `micro-dao` MILP optimizer is its ability to dynamically recalculate its minimum uptime constraints based on the real-time physical fragility of the hardware. 

You can use the `--health` flag to observe how the routing algorithm shifts its priority from **OPEX** (when the stack is new) to **CAPEX** (when the stack is degraded).

#### 1. The "Brand New" Stack (Prioritizes OPEX)

When the electrolyzer is brand new, the physical damage (and financial penalty) of a cold start is relatively low. The solver will willingly transition states more frequently to chase cheaper electricity prices.

```bash
uv run src/main.py --health 100.0 data/*
```

#### 2. The "End-of-Life" Stack (Prioritizes CAPEX)

As the stack degrades, it becomes highly sensitive to thermal cycling. The cost of a cold start becomes massive. If you start the simulation with a heavily degraded stack, you will see the MILP solver drastically stretch its minimum uptime constraints. It will actively choose to run through expensive electricity price peaks (paying higher OPEX) just to avoid the devastating CAPEX penalty of shutting down and restarting.
```bash
uv run src/main.py --health 15.0 data/*
```
