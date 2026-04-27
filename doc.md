# OS Process Scheduler Simulator — Documentation

A discrete-event simulation of eight classical CPU scheduling algorithms, augmented with
LRU cache modelling and context-switch overhead tracking.  
Built with **SimPy** (discrete-event engine), **Tkinter** (GUI), and pure Python.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [config.py — Simulation Constants & Theme Tokens](#3-configpy)
4. [main.py — Core Data Structures & Entry Point](#4-mainpy)
   - 4.1 [Process](#41-process)
   - 4.2 [RAM](#42-ram)
   - 4.3 [CacheManager](#43-cachemanager)
   - 4.4 [generate_processes()](#44-generate_processes)
   - 4.5 [run_algorithm()](#45-run_algorithm)
5. [algorithms.py — Scheduling Algorithms](#5-algorithmspy)
   - 5.1 [\_make_notifier()](#51-_make_notifier)
   - 5.2 [\_arrival()](#52-_arrival)
   - 5.3 [\_do_ctx_switch()](#53-_do_ctx_switch)
   - 5.4 [fcfs()](#54-fcfs)
   - 5.5 [sjf()](#55-sjf)
   - 5.6 [srtf()](#56-srtf)
   - 5.7 [priority_np()](#57-priority_np)
   - 5.8 [priority_p()](#58-priority_p)
   - 5.9 [round_robin()](#59-round_robin)
   - 5.10 [mlq()](#510-mlq)
   - 5.11 [mlfq()](#511-mlfq)
6. [gui.py — Graphical Interface](#6-guipy)
   - 6.1 [AlgorithmTab](#61-algorithmtab)
   - 6.2 [SchedulerGUI](#62-schedulergui)
7. [Timeline Event Format](#7-timeline-event-format)
8. [Data Flow](#8-data-flow)
9. [Extending the Simulator](#9-extending-the-simulator)

---

## 1. Project Overview

The simulator models an operating system's short-term scheduler. Each run:

- Randomly generates a set of processes, each with an arrival time, CPU burst,
  priority, RAM requirement, and CPU-cache working-set size.
- Passes those processes through all eight scheduling algorithms independently
  (each algorithm gets a deep copy and a fresh cache/RAM environment).
- Renders per-algorithm results in a tabbed GUI: a process results table,
  scheduler performance stats, cache hit/miss stats, and a Gantt chart that
  colour-codes CPU bursts and context-switch overhead bars.
- Prints a side-by-side comparison table to stdout when "Run All" is clicked.

---

## 2. Architecture

```
main.py
  ├── Process            – data class for one process
  ├── RAM                – SimPy Container wrapping total RAM
  ├── CacheManager       – LRU CPU-cache model
  ├── generate_processes – random workload generator
  └── run_algorithm      – dispatches to algorithms.py

algorithms.py
  ├── _make_notifier     – wake-up event helper (shared by all algorithms)
  ├── _arrival           – SimPy coroutine: process arrives, acquires RAM
  ├── _do_ctx_switch     – context-switch cost & cache lookup
  └── fcfs / sjf / srtf / priority_np / priority_p
      / round_robin / mlq / mlfq   – one SimPy coroutine per algorithm

config.py               – all magic numbers and colour constants

gui.py
  ├── AlgorithmTab       – one notebook tab (table + stats + Gantt)
  └── SchedulerGUI       – main Tk window (header, process list, notebook)
```

---

## 3. `config.py`

All tunable constants and colour tokens are centralised here so the rest of the
codebase imports them with `from config import *`.

### Simulation constants

| Name              | Default           | Meaning                                                                                                                         |
| ----------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `TOTAL_RAM_MB`    | `128`             | Total simulated RAM in MB. RAM is modelled as a finite pool; a process cannot start until it is granted its memory requirement. |
| `SIM_TIME`        | `50`              | Upper bound on the arrival-time window. Processes are generated with arrival times in `[1, SIM_TIME]`.                          |
| `QUANTUM`         | `3`               | Time-slice length (sim-ticks) used by Round Robin and the medium-priority queue in MLQ.                                         |
| `MLFQ_QUANTA`     | `{1:2, 2:4, 3:8}` | Per-level time quanta for MLFQ. Level 1 is the highest priority (shortest quantum); level 3 is the lowest (longest quantum).    |
| `AGING_THRESHOLD` | `12`              | Number of sim-ticks a process may wait in MLFQ levels 2 or 3 before being promoted one level to prevent starvation.             |

### Cache & context-switch constants

| Name                  | Default | Meaning                                                                                           |
| --------------------- | ------- | ------------------------------------------------------------------------------------------------- |
| `CACHE_CAPACITY`      | `128`   | Total working-set units the simulated CPU cache can hold simultaneously.                          |
| `CACHE_MISS_PENALTY`  | `2`     | Extra sim-ticks added to a context switch when the incoming process is not in cache (cold start). |
| `CONTEXT_SWITCH_COST` | `1`     | Base sim-ticks charged for every context switch (save/restore CPU state).                         |

### Colour tokens

Named constants (`BG`, `PANEL_BG`, `HDR_BG`, `ACCENT`, etc.) that the GUI
imports. Changing these values recolours the entire interface without touching
widget code.

### Palette dictionaries

`GANTT_PALETTE` maps each algorithm key (`"FCFS"`, `"SJF"`, …) to a list of
10 hex colours cycled across process rows in the Gantt chart.

`MLFQ_LEVEL_COLORS` / `MLFQ_LEVEL_LABELS` map MLFQ queue levels 1–3 to their
Gantt bar colour and legend label, respectively.

`CTX_HIT_COLOR` / `CTX_MISS_COLOR` are the Gantt bar colours for context-switch
overhead: dark-slate for a cache hit (cheap switch), dark-red for a cache miss
(expensive switch).

`STAT_COLORS` and `CACHE_STAT_COLORS` define the background colours of the stats
badge widgets in each tab.

---

## 4. `main.py`

### 4.1 `Process`

```python
class Process:
    pid            : str    # e.g. "P1"
    arrival        : int    # sim-tick at which the process arrives
    burst          : int    # total CPU time required (sim-ticks)
    priority       : int    # 1 = High, 2 = Medium, 3 = Low
    memory         : int    # RAM required in MB (16–64)
    cache_required : int    # working-set size in cache units (8–48)
    in_cache       : bool   # True while the process is resident in cache
    start          : int    # tick at which the process first ran (-1 = not yet)
    finish         : int    # tick at which the process completed
    waiting        : int    # total time spent waiting (not on CPU)
    turnaround     : int    # finish - arrival
```

`start`, `finish`, `waiting`, and `turnaround` are initially zero/`-1` and are
written by the scheduler coroutine during simulation.

---

### 4.2 `RAM`

```python
class RAM(simpy.Container):
    def allocate(self, mb) -> simpy.Event  # get(mb)  — blocks until memory is available
    def free(self, mb)     -> simpy.Event  # put(mb)
    @property used      -> int             # capacity − level
    @property available -> int             # level
```

`RAM` subclasses `simpy.Container` (a finite resource). `allocate` issues a
`get` request that blocks the arrival coroutine until enough RAM is free. `free`
returns memory after a process finishes.

---

### 4.3 `CacheManager`

Models an LRU CPU cache as an `OrderedDict` mapping `pid → cache_required`.

```python
class CacheManager:
    capacity : int     # max working-set units (from config.CACHE_CAPACITY)
    hits     : int     # cumulative cache hits
    misses   : int     # cumulative cache misses
    used     : int     # (property) currently occupied cache units
    hit_rate : float   # (property) hits / (hits + misses)
```

#### Methods

**`access(pid, cache_required) → bool`**  
Records a cache access for `pid`.

- **HIT** — `pid` is already in the cache. Moves it to the MRU end
  (recency refresh), increments `hits`, returns `True`.
- **MISS** — `pid` is absent. Increments `misses`. Calls `_make_room` to
  evict LRU entries until at least `cache_required` units are free, then admits
  the process. Returns `False`.

**`evict(pid) → None`**  
Forcibly removes `pid` from the cache (called when a process exits and its RAM
is freed). Safe to call when `pid` is not present.

**`is_in_cache(pid) → bool`**  
Non-destructive membership test — does **not** update LRU order.

**`reset() → None`**  
Clears all state; used between independent algorithm runs.

**`_make_room(needed) → None`** _(private)_  
Evicts entries from the LRU end of `_cache` until `capacity − used ≥ needed`.

---

### 4.4 `generate_processes(sim_time)`

```python
def generate_processes(sim_time: int) -> list[Process]
```

Generates a random workload by stepping through simulation time.

- Advances a cursor `t` by a random increment `randint(1, 5)` each iteration.
- Stops once `t > sim_time`.
- Each process receives: sequential PID (`P1`, `P2`, …), arrival = `t`,
  burst = `randint(2, 8)`, priority = `randint(1, 3)`,
  memory = `randint(16, 64)` MB, cache_required = `randint(8, 48)` units.
- Called by the GUI on startup and every time "Regenerate" is clicked.
  The GUI sets `random.seed` before calling so results are reproducible.

---

### 4.5 `run_algorithm(alg, processes_orig)`

```python
def run_algorithm(alg: str, processes_orig: list[Process]
                 ) -> tuple[list[Process], list[tuple], CacheManager]
```

Runs one algorithm and returns its results.

1. Deep-copies `processes_orig` so each algorithm works on an identical,
   independent set of processes.
2. Creates a fresh `CacheManager`, SimPy `Environment`, and `RAM` instance.
3. Starts the appropriate scheduler coroutine and calls `env.run()` to
   exhaustion.
4. Returns `(procs, timeline, cache)`:
   - `procs` — the completed process list with all timing metrics filled in.
   - `timeline` — ordered list of events recorded during simulation
     (see [Section 7](#7-timeline-event-format)).
   - `cache` — the `CacheManager` instance with final `hits`/`misses`.

Valid `alg` strings: `"FCFS"`, `"SJF"`, `"SRTF"`, `"PRIO_NP"`, `"PRIO_P"`,
`"RR"`, `"MLQ"`, `"MLFQ"`.

---

## 5. `algorithms.py`

All scheduler functions are SimPy generator coroutines started with
`env.process(...)`. They share two helper functions and a common arrival
coroutine.

---

### 5.1 `_make_notifier(env)`

```python
def _make_notifier(env) -> tuple[list[simpy.Event], Callable]
```

Creates a sleep/wake-up pair so a scheduler coroutine can block
when the ready queue is empty and be woken at the exact tick a new
process arrives (rather than wasting ticks with `timeout(0)` polling).

**Returns** `(wakeup_ref, notify)`:

- `wakeup_ref` — a one-element list holding the current unarmed SimPy event.
  A list is used instead of a plain variable so the inner `notify` closure can
  update the reference.
- `notify()` — callable that triggers `wakeup_ref[0]` only if it has not
  already fired (safe to call multiple times in the same tick).

**Scheduler loop pattern:**

```python
while not done:
    if ready_queue_is_empty:
        yield wakeup_ref[0]          # sleep until something arrives
        wakeup_ref[0] = env.event()  # re-arm for next sleep
        continue
    ... schedule next process ...
    notify()   # wake self if new jobs arrived during the last burst
```

---

### 5.2 `_arrival(env, p, ram, on_ready, notify, timeline)`

SimPy coroutine that models one process arriving and entering the ready queue.

1. `yield env.timeout(max(0, p.arrival - env.now))` — waits until the process's
   arrival time.
2. `yield ram.allocate(p.memory)` — blocks until enough RAM is available.
3. Appends a `("ram", now, pid, memory)` event to `timeline`.
4. Calls `on_ready(p)` to insert the process into whichever data structure the
   scheduler uses (a list, priority queue, or dictionary of queues).
5. Calls `notify()` to wake the scheduler.

---

### 5.3 `_do_ctx_switch(p, prev_pid, cache)`

```python
def _do_ctx_switch(p, prev_pid, cache) -> tuple[int, bool]
                                          # (penalty_ticks, is_hit)
```

Calculates the context-switch overhead when the CPU is handed to process `p`.

| Situation                                    | penalty_ticks                                  | is_hit  |
| -------------------------------------------- | ---------------------------------------------- | ------- |
| Same process continues (`prev_pid == p.pid`) | `0`                                            | `True`  |
| Different process, cache HIT                 | `CONTEXT_SWITCH_COST` (1)                      | `True`  |
| Different process, cache MISS                | `CONTEXT_SWITCH_COST + CACHE_MISS_PENALTY` (3) | `False` |

Always calls `cache.access(p.pid, p.cache_required)`, which updates LRU
recency and the hit/miss counters. Sets `p.in_cache = True`.

The **caller** is responsible for:

1. `yield env.timeout(penalty_ticks)` if `penalty_ticks > 0`.
2. Appending a `("ctx_switch", pid, t0, now, is_hit)` event to `timeline`.

---

### 5.4 `fcfs()`

**First-Come, First-Served** — non-preemptive.

```python
def fcfs(env, processes, ram, cache, timeline)
```

Processes are served in arrival order. A process that arrives while
the CPU is busy waits in `ready` (a plain list used as a FIFO queue).
Once a process starts it runs to completion without interruption.

**Waiting time** = `start − arrival`.  
**Turnaround** = `finish − arrival`.

---

### 5.5 `sjf()`

**Shortest Job First** — non-preemptive.

```python
def sjf(env, processes, ram, cache, timeline)
```

At every scheduling decision, `ready` is sorted ascending by `burst` and the
shortest job is selected. Like FCFS, once running a process cannot be
preempted. May cause starvation of long processes if short ones keep arriving.

---

### 5.6 `srtf()`

**Shortest Remaining Time First** — preemptive SJF.

```python
def srtf(env, processes, ram, cache, timeline)
```

Executes in 1-tick steps. After each tick, the current process is re-inserted
into `ready` and the ready list is re-sorted by `remaining[pid]`. If a newly
arrived process has a shorter remaining time than the running one, it preempts
it. A context-switch check (`_do_ctx_switch`) is made at every preemption
point.

**Turnaround** = `finish − arrival`.  
**Waiting** = `turnaround − burst`.

---

### 5.7 `priority_np()`

**Non-Preemptive Priority Scheduling.**

```python
def priority_np(env, processes, ram, cache, timeline)
```

At each scheduling decision, `ready` is sorted ascending by `priority`
(1 = highest) and the highest-priority process is selected. Once running,
it executes to completion. Equal-priority processes are served in the order
they entered the ready queue (FIFO tie-break preserved by stable sort).

---

### 5.8 `priority_p()`

**Preemptive Priority Scheduling.**

```python
def priority_p(env, processes, ram, cache, timeline)
```

Runs in 1-tick steps. After each tick, re-sorts by `priority` so a
higher-priority process that just arrived can preempt the running one.
Context-switch overhead is charged on every preemption.

---

### 5.9 `round_robin()`

**Round Robin.**

```python
def round_robin(env, processes, ram, cache, quantum, timeline)
```

Each process is given a time slice of `quantum` ticks. If it does not finish
within the slice, it is re-appended to the tail of `ready` and a new slice
begins with the next process. New arrivals that enter while the CPU is busy
are added to the tail of `ready`, so they are served after all currently
waiting processes (standard FIFO enqueue).

A context-switch penalty is charged whenever a different process is selected.

---

### 5.10 `mlq()`

**Multi-Level Queue.**

```python
def mlq(env, processes, ram, cache, quantum, timeline)
```

Processes are permanently assigned to one of three fixed queues based on their
static `priority` (1 = High, 2 = Med, 3 = Low). The scheduler always picks
from the highest non-empty queue.

- **Queue 1 (High):** Non-preemptive — runs the full burst.
- **Queue 2 (Med):** Round Robin with `quantum` ticks.
- **Queue 3 (Low):** Non-preemptive — runs the full burst.

A process never moves between queues; starvation of lower queues is possible
when the high-priority queue is continuously busy.

---

### 5.11 `mlfq()`

**Multi-Level Feedback Queue.**

```python
def mlfq(env, processes, ram, cache, timeline)
```

Processes are dynamic: they start at level 1 (shortest quantum) and can be
demoted or promoted based on behaviour.

**Queue levels and quanta** (from `MLFQ_QUANTA`):

| Level | Quantum | Colour |
| ----- | ------- | ------ |
| 1     | 2 ticks | Red    |
| 2     | 4 ticks | Orange |
| 3     | 8 ticks | Slate  |

**Demotion:** If a process exhausts its full time quantum, it is moved down one
level (more CPU-intensive processes migrate toward longer quanta). A
`("demote", now, pid, old_level, new_level)` event is appended to `timeline`.

**Promotion (Aging):** Before each scheduling decision, all processes in
levels 2 and 3 whose `last_cpu` timestamp is `≥ AGING_THRESHOLD` ticks ago are
promoted one level to prevent starvation. A
`("promote", now, pid, old_level, new_level)` event is recorded.

**Gantt colour coding:** CPU bars use `MLFQ_LEVEL_COLORS` (queue level colour),
not the per-process palette used by other algorithms. Demotion is marked
with a red ▼ triangle above the bar; promotion with a green ▲ triangle below.

**`prev_pid` tracking:** `prev_pid` records the last PID that actually occupied
the CPU. If the same process is immediately re-selected (no preemption), no
context-switch penalty is charged.

---

## 6. `gui.py`

### 6.1 `AlgorithmTab`

```python
class AlgorithmTab(tk.Frame)
```

One notebook tab, rendered for a single algorithm. Created by `SchedulerGUI`
for each of the eight algorithms.

#### Layout (top-to-bottom)

1. **Results table** — `ttk.Treeview` with columns:
   PID, Arrival, Burst, Priority, Memory, Cache (working-set), Start, Finish,
   Wait, TAT. Rows are colour-coded by priority tag (`H`/`M`/`L`).

2. **Scheduler stats strip** — three badge labels:
   - _Avg Wait_ — mean waiting time across all processes.
   - _Avg TAT_ — mean turnaround time.
   - _Throughput_ — `n_processes / max_completion_tick`.
   - MLFQ tabs additionally show _Demotions_ and _Promotions_ counts, plus a
     level colour legend.

3. **Cache stats strip** — three badge labels:
   - _Cache Hits_, _Cache Misses_, _Hit Rate_.
   - A context-switch legend shows the meaning of CTX-Hit and CTX-Miss bar
     colours.

4. **Gantt chart** — scrollable `tk.Canvas` with horizontal and vertical
   scrollbars. Mouse-wheel scrolls vertically; Shift+Mouse-wheel scrolls
   horizontally.

#### Public methods

**`clear()`**  
Resets the table, stats badges, and canvas to their initial `"—"` placeholder
state. Called when a new process set is generated.

**`update(procs, timeline, cache)`**  
Populates all four sections from simulation results:

- `_fill_table(procs)` — inserts one row per process.
- `_fill_stats(procs, timeline)` — computes and sets the stats badges.
- `_fill_cache_stats(cache)` — sets cache hit/miss/rate badges.
- `_draw_gantt(procs, timeline)` — renders the Gantt chart.

#### Gantt rendering internals

**`_draw_gantt(procs, timeline)`**  
Entry point. Separates `cpu` and `ctx_switch` events, computes `max_t`
(using both), then delegates to the algorithm-specific renderer.

**`_draw_gantt_standard(cpu, ctx, max_t, …)`**  
Used by all algorithms except MLFQ. Assigns each PID to a horizontal row in
order of first CPU appearance. Draws context-switch bars first (below), then
CPU burst bars on top. Each process has a consistent colour from the
algorithm's `GANTT_PALETTE`.

**`_draw_gantt_mlfq(cpu, ctx, timeline, max_t, …)`**  
MLFQ-specific renderer. CPU bars are coloured by queue level instead of by
PID. Reads `("demote", …)` and `("promote", …)` events from `timeline` and
draws ▼/▲ markers at the correct positions.

**`_draw_grid(max_t, LEFT, TOP, H, PX)`**  
Draws the time-axis grid lines (major lines every 5 ticks, minor lines every
tick) and tick labels.

---

### 6.2 `SchedulerGUI`

```python
class SchedulerGUI(tk.Tk)
```

Main application window.

**Constructor parameters:**

- `generate_fn` — callable matching `generate_processes(sim_time)`.
- `run_fn` — callable matching `run_algorithm(alg, processes)`.

These are injected from `main.py` to keep the GUI decoupled from the simulation
logic.

#### Layout

**Header bar** (`HDR_BG` background):

- Title label: _"⚙ OS Process Scheduler Simulator + Cache"_.
- Seed spinner (`tk.Spinbox`, range 0–9999, default 42).
- _↺ Regenerate_ button — re-seeds `random`, generates a new process list,
  resets all tabs.
- _▶ Run All_ button — runs all eight algorithms, updates every tab, and prints
  a comparison table to stdout.

**Sub-header bar** (`SUBHDR_BG` background):  
Displays the active configuration values (RAM, cache capacity, context-switch
cost, miss penalty, quantum settings, sim window, aging threshold) as read-only
labels.

**Body** (`tk.PanedWindow`, horizontal split):

- **Left pane — Process List** (default 290 px wide, min 210 px):  
  `ttk.Treeview` showing the generated processes (PID, Arrival, Burst, Priority,
  Memory, Cache). Colour-coded by priority. A legend strip shows High/Med/Low
  colours.

- **Right pane — Notebook** (min 500 px):  
  Eight `AlgorithmTab` instances, one per algorithm, in tabs labeled
  `FCFS`, `SJF`, `SRTF`, `Priority (NP)`, `Priority (P)`,
  `Round Robin (q=N)`, `MLQ`, `MLFQ`.

#### Key methods

**`_setup_styles()`**  
Configures `ttk.Style` for all widget types (`Treeview`, `Notebook`, scrollbars)
with the colour tokens from `config.py`.

**`_regenerate()`**  
Sets `random.seed(self._seed_var.get())`, calls `generate_fn(SIM_TIME)`, stores
the new process list, repopulates the process list treeview, and calls
`tab.clear()` on every algorithm tab.

**`_populate_ptree()`**  
Refills the left-pane process list treeview from `self._procs`.

**`_run_all()`**  
Iterates over all eight algorithm keys, calls `run_fn(alg, self._procs)` for
each, passes results to `tab.update()`, collects per-algorithm metrics, then
calls `_print_comparison()`.

**`_print_comparison(results)`**  
Prints a formatted comparison table to stdout with columns:
Avg Wait, Avg TAT, Throughput, CTX Switches, Cache Hits, Cache Misses, Hit Rate.
Identifies and prints the winner in five categories:
Lowest Waiting Time, Lowest Turnaround Time, Highest Throughput,
Highest Cache Hit Rate, and Fewest Context Switches.

---

## 7. Timeline Event Format

The `timeline` list is populated during simulation and consumed by the GUI.
Each element is a tuple whose first field is the event type:

| Type           | Tuple structure                                   | When appended                                          |
| -------------- | ------------------------------------------------- | ------------------------------------------------------ |
| `"ram"`        | `("ram", now, pid, memory_mb)`                    | Process admitted to ready queue after RAM allocation   |
| `"ctx_switch"` | `("ctx_switch", pid, t_start, t_end, is_hit)`     | Context-switch overhead period (before each CPU burst) |
| `"cpu"`        | `("cpu", pid, t_start, t_end, priority_or_level)` | CPU burst slice completes                              |
| `"demote"`     | `("demote", now, pid, old_level, new_level)`      | MLFQ: process moved to a lower-priority queue          |
| `"promote"`    | `("promote", now, pid, old_level, new_level)`     | MLFQ: process aged up to a higher-priority queue       |

> **Note on the `"cpu"` 5th field:** For all algorithms except MLFQ, the 5th
> field is the process's static `priority` (1–3). For MLFQ it is the queue
> _level_ at the time of execution, used for level-based Gantt colouring.

---

## 8. Data Flow

```
User clicks "Run All"
         │
         ▼
SchedulerGUI._run_all()
  └── for each alg in (FCFS, SJF, …, MLFQ):
        run_algorithm(alg, self._procs)
          ├── deepcopy(processes_orig)
          ├── CacheManager(CACHE_CAPACITY)
          ├── simpy.Environment()
          ├── RAM(env, TOTAL_RAM_MB)
          ├── env.process(<algorithm coroutine>)
          │     ├── _arrival() coroutines (one per process)
          │     │     └── wait for arrival tick
          │     │         wait for RAM
          │     │         on_ready(p) / notify()
          │     └── scheduler loop
          │           ├── _do_ctx_switch()  → cache.access()
          │           ├── yield timeout(penalty)
          │           ├── yield timeout(burst_or_quantum)
          │           └── cache.evict() / ram.free()
          └── env.run()
              returns (procs, timeline, cache)
        AlgorithmTab.update(procs, timeline, cache)
          ├── _fill_table()
          ├── _fill_stats()
          ├── _fill_cache_stats()
          └── _draw_gantt()
```

---

## 9. Extending the Simulator

### Adding a new scheduling algorithm

1. Write a SimPy generator function in `algorithms.py` with the signature:

   ```python
   def my_alg(env, processes, ram, cache, timeline):
       ...
   ```

   Use `_make_notifier`, `_arrival`, and `_do_ctx_switch` as shown in the
   existing algorithms.

2. Add a dispatch branch in `run_algorithm()` in `main.py`:

   ```python
   elif alg == "MY_ALG":
       env.process(my_alg(env, procs, ram, cache, timeline))
   ```

3. Add a Gantt colour palette in `config.py`:

   ```python
   GANTT_PALETTE["MY_ALG"] = ["#hex1", "#hex2", ...]
   ```

4. Add a tab in `SchedulerGUI._build_body()`:

   ```python
   ("MY_ALG", "  My Algorithm  "),
   ```

5. Add the algorithm key to the loop in `SchedulerGUI._run_all()`.

### Changing simulation parameters

All timing, resource, and visual parameters live in `config.py`. No other
file needs to be edited for parameter changes.

### Changing the process generator

Replace or augment `generate_processes()` in `main.py`. The only contract is
that it returns a `list[Process]`. The GUI calls it as
`generate_fn(SIM_TIME)`.
