"""
OS Process Scheduler Simulator — with CPU Cache Simulation
===========================================================

NEW in this version

  CacheManager     LRU-eviction cache that is fully independent of both the
                   scheduler and the RAM manager. Schedulers *query* the cache;
                   they do not own it.

  Per-process      cache_required  – working-set size (cache units)
                   in_cache        – flag updated by scheduler on each access

  Context switch   Every time the CPU switches to a *different* process the
                   scheduler calls _do_ctx_switch(), which:
                     • checks CacheManager (hit → cheap, miss → expensive)
                     • yields an env.timeout(penalty) before the real burst
                     • appends a ("ctx_switch", …) timeline entry

  Timeline events  "cpu"        – actual CPU burst  (unchanged)
                   "ctx_switch" – overhead slice coloured dark in Gantt
                   "ram"        – RAM allocation     (unchanged)
                   "demote"     – MLFQ demotion      (unchanged)
                   "promote"    – MLFQ promotion     (unchanged)

  Gantt chart      Context-switch bars drawn in the same row as the process:
                     dark slate  → cache HIT  (cheap, 1 tick)
                     dark red    → cache MISS (costly, 1+2 = 3 ticks)

  Stats strip      Per-algorithm cache hits / misses / hit-rate badges.
"""

import simpy
import random
import tkinter as tk
from tkinter import ttk
from copy import deepcopy
from collections import OrderedDict

# ─ simulation constants 
TOTAL_RAM_MB       = 128
SIM_TIME           = 50
QUANTUM            = 3
MLFQ_QUANTA        = {1: 2, 2: 4, 3: 8}
AGING_THRESHOLD    = 12

#  cache & context-switch constants 
CACHE_CAPACITY      = 128   # total working-set units the CPU cache can hold
CACHE_MISS_PENALTY  = 2     # extra sim-ticks charged on a cold cache miss
CONTEXT_SWITCH_COST = 1     # base sim-ticks to save/restore CPU context

#  colour tokens 
BG          = "#f0f4f8"
PANEL_BG    = "#ffffff"
HDR_BG      = "#0f172a"
SUBHDR_BG   = "#1e293b"
ACCENT      = "#6366f1"
ACCENT_DARK = "#4f46e5"
TEXT_DARK   = "#0f172a"
TEXT_MID    = "#64748b"
TEXT_LIGHT  = "#94a3b8"
BORDER      = "#e2e8f0"

PRIO_LABEL = {1: "High", 2: "Med", 3: "Low"}

GANTT_PALETTE = {
    "FCFS": ["#3b82f6","#2563eb","#1d4ed8","#0ea5e9","#0284c7",
             "#60a5fa","#0369a1","#38bdf8","#075985","#7dd3fc"],
    "SJF":  ["#f59e0b","#d97706","#b45309","#fbbf24","#92400e",
             "#fcd34d","#78350f","#fde68a","#a16207","#fed7aa"],
    "RR":   ["#a855f7","#9333ea","#7e22ce","#c084fc","#6d28d9",
             "#8b5cf6","#581c87","#7c3aed","#4c1d95","#a78bfa"],
    "MLQ":  ["#10b981","#059669","#047857","#0d9488","#0f766e",
             "#34d399","#065f46","#14b8a6","#115e59","#2dd4bf"],
    "MLFQ": ["#ef4444","#f97316","#94a3b8"],
}
GANTT_PALETTE["SRTF"]    = GANTT_PALETTE["SJF"]
GANTT_PALETTE["PRIO_NP"] = GANTT_PALETTE["MLQ"]
GANTT_PALETTE["PRIO_P"]  = GANTT_PALETTE["MLQ"]

# context-switch Gantt slice colours
CTX_HIT_COLOR  = "#334155"   # dark slate  → cache HIT  (cheap)
CTX_MISS_COLOR = "#991b1b"   # dark red    → cache MISS (expensive)

MLFQ_LEVEL_COLORS = {1: "#ef4444", 2: "#f97316", 3: "#94a3b8"}
MLFQ_LEVEL_LABELS = {1: "Q1 (τ=2)", 2: "Q2 (τ=4)", 3: "Q3 (τ=8)"}

STAT_COLORS       = ["#3b82f6", "#8b5cf6", "#10b981"]
CACHE_STAT_COLORS = ["#0ea5e9", "#f43f5e", "#7c3aed"]


# ═
#  CacheManager  —  completely independent of Scheduler and MemoryManager
# ═
class CacheManager:
    """
    Fixed-capacity CPU cache with Least-Recently-Used (LRU) eviction.

    Design goals
    
    • Decoupled: neither the scheduler nor the RAM manager imports this
      class; they receive a CacheManager instance at construction time.
    • Schedulers QUERY cache state (access / is_in_cache); they do NOT
      manage it.  The only external write path (besides access) is evict(),
      called by the scheduler after a process's RAM is freed.

    Data model
    
    _cache : OrderedDict[pid → cache_required]
        LRU order — rightmost = most recently used.
        OrderedDict.move_to_end(key) promotes on hit.
        OrderedDict.popitem(last=False) evicts the LRU entry.

    _used : int
        Running total of occupied cache units.

    Public API
    
    access(pid, cache_required) → bool
        True  = cache HIT  (process was warm; recency refreshed).
        False = cache MISS (process admitted; LRU evictions as needed).

    evict(pid)
        Forcibly remove a process.  Must be called when the process's RAM
        page is reclaimed so a stale working-set doesn't occupy cache space.

    is_in_cache(pid) → bool
        Non-destructive membership test (does NOT count as an access).

    Properties
    
    hits, misses   cumulative counters
    hit_rate       float in [0, 1]
    used           currently occupied units

    reset()        zero stats and clear the cache between runs.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._cache   = OrderedDict()   # pid → cache_required (LRU ordered)
        self._used    = 0
        self.hits     = 0
        self.misses   = 0

    #  public 

    def access(self, pid: str, cache_required: int) -> bool:
        """
        Record a cache access for ``pid``.

        HIT  → move to MRU end, increment hits,  return True.
        MISS → evict LRU entries until room exists, admit, return False.
        """
        if pid in self._cache:
            self._cache.move_to_end(pid)        # refresh recency
            self.hits += 1
            return True

        #  MISS path 
        self.misses += 1
        self._make_room(cache_required)
        self._cache[pid] = cache_required
        self._used += cache_required
        return False

    def evict(self, pid: str) -> None:
        """
        Forcibly remove ``pid`` from cache (e.g. process page freed from RAM).

        Safe to call even if ``pid`` is not currently cached.
        """
        if pid in self._cache:
            self._used -= self._cache.pop(pid)

    def is_in_cache(self, pid: str) -> bool:
        """Non-destructive membership test — does NOT update LRU order."""
        return pid in self._cache

    #  properties 

    @property
    def used(self) -> int:
        return self._used

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def reset(self) -> None:
        self._cache.clear()
        self._used  = 0
        self.hits   = 0
        self.misses = 0

    #  private 

    def _make_room(self, needed: int) -> None:
        """Evict LRU entries until at least ``needed`` units are free."""
        while self._used + needed > self.capacity and self._cache:
            _pid, size = self._cache.popitem(last=False)   # evict oldest
            self._used -= size


class RAM(simpy.Container):
    def __init__(self, env, total_mb):
        super().__init__(env, capacity=total_mb, init=total_mb)

    def allocate(self, mb): return self.get(mb)
    def free(self, mb):     return self.put(mb)

    @property
    def used(self):      return int(self.capacity - self.level)
    @property
    def available(self): return int(self.level)



class Process:
    def __init__(self, pid, arrival, burst, priority, memory, cache_required):
        self.pid            = pid
        self.arrival        = arrival
        self.burst          = burst
        self.priority       = priority
        self.memory         = memory
        #  new cache fields ─
        self.cache_required = cache_required   # working-set size (cache units)
        self.in_cache       = False            # updated by scheduler on access
        #  timing metrics 
        self.start      = -1
        self.finish     = 0
        self.waiting    = 0
        self.turnaround = 0


def generate_processes(sim_time):
    procs, t, pid = [], 0, 1
    while t < sim_time:
        t += random.randint(1, 5)
        if t > sim_time:
            break
        procs.append(Process(
            pid=f"P{pid}",
            arrival=t,
            burst=random.randint(2, 8),
            priority=random.randint(1, 3),
            memory=random.randint(16, 64),
            cache_required=random.randint(8, 48),   # working-set units
        ))
        pid += 1
    return procs

def _make_notifier(env):
    """
    Wake-up event pair so the scheduler sleeps when the ready queue is empty
    and wakes at the *exact* tick a new process arrives.
    """
    ref = [env.event()]
    def notify():
        if not ref[0].triggered:
            ref[0].succeed()
    return ref, notify


def _arrival(env, p, ram, on_ready, notify, timeline):
    yield env.timeout(max(0, p.arrival - env.now))
    yield ram.allocate(p.memory)
    timeline.append(("ram", env.now, p.pid, p.memory))
    on_ready(p)
    notify()


# ═
#  Context-switch / cache helper
#  
#  Integration point: every scheduler calls this helper ONCE per scheduling
#  decision, immediately before yielding the actual CPU burst.
#
#  The scheduler remains ignorant of *how* the cache works; it only receives
#  (penalty_ticks, is_hit) and decides what to do with the overhead.
# ═
def _do_ctx_switch(p: Process, prev_pid, cache: CacheManager):
    """
    Compute the context-switch overhead for switching the CPU to process ``p``.

    Parameters
    
    p        : the process about to run
    prev_pid : pid of the last process that held the CPU (None if idle)
    cache    : the shared CacheManager instance

    Returns
    
    (penalty_ticks : int, is_hit : bool)

    Cases
    
    Same process continues  → (0,    True)   no switch, no cost
    Different process, HIT  → (1,    True)   save/restore only
    Different process, MISS → (1+2,  False)  save/restore + cold-start penalty

    The caller is responsible for:
      1. yield env.timeout(penalty_ticks)   if penalty_ticks > 0
      2. timeline.append(("ctx_switch", …)) for Gantt visualisation
    """
    if prev_pid is not None and prev_pid == p.pid:
        return 0, True                          # same process — no cost

    is_hit     = cache.access(p.pid, p.cache_required)
    p.in_cache = True                           # process is now in cache
    penalty    = CONTEXT_SWITCH_COST + (0 if is_hit else CACHE_MISS_PENALTY)
    return penalty, is_hit


# ═
#  Scheduling algorithms  —  each now accepts `cache` as a parameter
#  Integration points:
#    ① _do_ctx_switch()  called before every CPU burst
#    ② cache.evict(pid)  called after every RAM free (process done)
# ═

#  FCFS 
def fcfs(env, processes, ram, cache, timeline):
    ready = []
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done, prev_pid = 0, None
    while done < len(processes):
        if not ready:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        p = ready.pop(0)

        # ① context switch + cache check
        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        t0 = env.now
        yield env.timeout(p.burst)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        p.finish     = env.now
        p.waiting    = p.start - p.arrival
        p.turnaround = p.finish - p.arrival
        prev_pid     = p.pid
        done        += 1

        # ② evict from cache when process leaves system
        cache.evict(p.pid); p.in_cache = False
        yield ram.free(p.memory)
        yield env.timeout(0); notify()


#  SJF 
def sjf(env, processes, ram, cache, timeline):
    ready = []
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done, prev_pid = 0, None
    while done < len(processes):
        if not ready:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        ready.sort(key=lambda x: x.burst)
        p = ready.pop(0)

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        t0 = env.now
        yield env.timeout(p.burst)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        p.finish     = env.now
        p.waiting    = p.start - p.arrival
        p.turnaround = p.finish - p.arrival
        prev_pid     = p.pid
        done        += 1

        cache.evict(p.pid); p.in_cache = False
        yield ram.free(p.memory)
        yield env.timeout(0); notify()


#  SRTF  (preemptive SJF) 
def srtf(env, processes, ram, cache, timeline):
    ready     = []
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done, current = 0, None
    while done < len(processes):
        if not ready and current is None:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        # derive prev_pid BEFORE pushing current back into ready
        prev_pid = current.pid if current else None
        if current:
            ready.append(current)

        ready.sort(key=lambda x: remaining[x.pid])
        p = ready.pop(0)

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        current = p
        t0      = env.now
        yield env.timeout(1)
        remaining[p.pid] -= 1
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done        += 1
            current      = None
            cache.evict(p.pid); p.in_cache = False
            yield ram.free(p.memory)
            yield env.timeout(0)
        notify()


#  Priority NP 
def priority_np(env, processes, ram, cache, timeline):
    ready = []
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done, prev_pid = 0, None
    while done < len(processes):
        if not ready:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        ready.sort(key=lambda x: x.priority)
        p = ready.pop(0)

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        t0 = env.now
        yield env.timeout(p.burst)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        p.finish     = env.now
        p.waiting    = p.start - p.arrival
        p.turnaround = p.finish - p.arrival
        prev_pid     = p.pid
        done        += 1

        cache.evict(p.pid); p.in_cache = False
        yield ram.free(p.memory)
        yield env.timeout(0); notify()


#  Priority P  (preemptive) 
def priority_p(env, processes, ram, cache, timeline):
    ready     = []
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done, current = 0, None
    while done < len(processes):
        if not ready and current is None:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        prev_pid = current.pid if current else None
        if current:
            ready.append(current)

        ready.sort(key=lambda x: x.priority)
        p = ready.pop(0)

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        current = p
        t0      = env.now
        yield env.timeout(1)
        remaining[p.pid] -= 1
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done        += 1
            current      = None
            cache.evict(p.pid); p.in_cache = False
            yield ram.free(p.memory)
            yield env.timeout(0)
        notify()


#  Round Robin 
def round_robin(env, processes, ram, cache, quantum, timeline):
    ready     = []
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done, prev_pid = 0, None
    while done < len(processes):
        if not ready:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        p = ready.pop(0)

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        run = min(quantum, remaining[p.pid])
        t0  = env.now
        yield env.timeout(run)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        remaining[p.pid] -= run
        prev_pid          = p.pid

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done        += 1
            cache.evict(p.pid); p.in_cache = False
            yield ram.free(p.memory)
            yield env.timeout(0)
        else:
            ready.append(p)
        notify()


#  MLQ 
def mlq(env, processes, ram, cache, quantum, timeline):
    queues    = {1: [], 2: [], 3: []}
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)

    def on_ready(p): queues[p.priority].append(p)
    for p in processes:
        env.process(_arrival(env, p, ram, on_ready, notify, timeline))

    done, prev_pid = 0, None
    while done < len(processes):
        prio = next((lvl for lvl in (1, 2, 3) if queues[lvl]), None)
        if prio is None:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        p = queues[prio].pop(0)

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        run = min(quantum, remaining[p.pid]) if prio == 2 else remaining[p.pid]
        t0  = env.now
        yield env.timeout(run)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        remaining[p.pid] -= run
        prev_pid          = p.pid

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done        += 1
            cache.evict(p.pid); p.in_cache = False
            yield ram.free(p.memory)
            yield env.timeout(0)
        else:
            queues[prio].append(p)
        notify()


#  MLFQ 
def mlfq(env, processes, ram, cache, timeline):
    """
    Multi-Level Feedback Queue with dynamic priority, aging, AND cache-aware
    context switching.

    prev_pid tracks the last PID that actually occupied the CPU.  When the
    same process is re-selected (e.g. it was re-queued at the same level and
    immediately wins), NO context-switch penalty is charged.
    """
    queues    = {1: [], 2: [], 3: []}
    levels    = {}
    last_cpu  = {}
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)

    def on_ready(p):
        queues[1].append(p)
        levels[p.pid]   = 1
        last_cpu[p.pid] = env.now

    for p in processes:
        env.process(_arrival(env, p, ram, on_ready, notify, timeline))

    done, prev_pid = 0, None
    while done < len(processes):
        #  aging pass 
        now = env.now
        for lvl in (2, 3):
            aged = [p for p in queues[lvl]
                    if now - last_cpu[p.pid] >= AGING_THRESHOLD]
            for p in aged:
                queues[lvl].remove(p)
                new_lvl = lvl - 1
                levels[p.pid] = new_lvl
                queues[new_lvl].append(p)
                timeline.append(("promote", now, p.pid, lvl, new_lvl))

        prio = next((lvl for lvl in (1, 2, 3) if queues[lvl]), None)
        if prio is None:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        p       = queues[prio].pop(0)
        quantum = MLFQ_QUANTA[prio]
        run     = min(quantum, remaining[p.pid])

        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now
        t0 = env.now
        yield env.timeout(run)
        # 5th field = queue LEVEL (not original priority) for colour-coding
        timeline.append(("cpu", p.pid, t0, env.now, prio))

        last_cpu[p.pid]   = env.now
        remaining[p.pid] -= run
        prev_pid           = p.pid

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done        += 1
            cache.evict(p.pid); p.in_cache = False
            yield ram.free(p.memory)
            yield env.timeout(0)
        else:
            if run == quantum:
                new_lvl = min(prio + 1, 3)
                levels[p.pid] = new_lvl
                queues[new_lvl].append(p)
                timeline.append(("demote", env.now, p.pid, prio, new_lvl))
            else:
                queues[prio].append(p)
        notify()


# ═
#  Runner  —  returns (procs, timeline, cache) so the GUI can display
#             per-algorithm cache statistics
# ═
def run_algorithm(alg, processes_orig):
    procs    = deepcopy(processes_orig)
    timeline = []
    cache    = CacheManager(CACHE_CAPACITY)   # fresh cache per run
    env      = simpy.Environment()
    ram      = RAM(env, TOTAL_RAM_MB)

    if alg == "FCFS":
        env.process(fcfs(env, procs, ram, cache, timeline))
    elif alg == "SJF":
        env.process(sjf(env, procs, ram, cache, timeline))
    elif alg == "SRTF":
        env.process(srtf(env, procs, ram, cache, timeline))
    elif alg == "PRIO_NP":
        env.process(priority_np(env, procs, ram, cache, timeline))
    elif alg == "PRIO_P":
        env.process(priority_p(env, procs, ram, cache, timeline))
    elif alg == "RR":
        env.process(round_robin(env, procs, ram, cache, QUANTUM, timeline))
    elif alg == "MLQ":
        env.process(mlq(env, procs, ram, cache, QUANTUM, timeline))
    elif alg == "MLFQ":
        env.process(mlfq(env, procs, ram, cache, timeline))

    env.run()
    return procs, timeline, cache   # ← cache returned for GUI stats display


# ═
#  GUI
# ═

class AlgorithmTab(tk.Frame):
    """One notebook tab: results table + stats strips (scheduler + cache) + Gantt."""

    def __init__(self, parent, alg):
        super().__init__(parent, bg=BG)
        self.alg = alg
        self._build()

    #  layout 
    def _build(self):
        #  results table ─
        rf = tk.Frame(self, bg=PANEL_BG, highlightthickness=1,
                      highlightbackground=BORDER)
        rf.pack(fill="x", padx=8, pady=(8, 3))

        tk.Label(rf, text="Results", bg=PANEL_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 3))

        tree_wrap = tk.Frame(rf, bg=PANEL_BG)
        tree_wrap.pack(fill="x", padx=6, pady=(0, 6))

        cols   = ("pid", "arr", "bst", "pri", "mem", "cache", "start", "fin", "wait", "tat")
        hdrs   = ("PID", "Arr", "Burst", "Prio", "Mem", "Cache", "Start", "Finish", "Wait", "TAT")
        widths = (44, 40, 48, 48, 54, 50, 50, 54, 46, 46)

        self._rtree = ttk.Treeview(tree_wrap, columns=cols,
                                   show="headings", height=7,
                                   selectmode="none")
        for c, h, w in zip(cols, hdrs, widths):
            self._rtree.heading(c, text=h)
            self._rtree.column(c, width=w, minwidth=w,
                               anchor="center", stretch=False)

        rsb = ttk.Scrollbar(tree_wrap, orient="vertical",
                            command=self._rtree.yview)
        self._rtree.configure(yscrollcommand=rsb.set)
        self._rtree.pack(side="left", fill="x", expand=True)
        rsb.pack(side="right", fill="y")

        self._rtree.tag_configure("H", background="#fff1f2", foreground="#9f1239")
        self._rtree.tag_configure("M", background="#fffbeb", foreground="#92400e")
        self._rtree.tag_configure("L", background="#f0fdf4", foreground="#166534")

        #  scheduler stats strip 
        sf = tk.Frame(self, bg=BG)
        sf.pack(fill="x", padx=8, pady=(0, 2))

        self._stat_vars = []
        for lbl, color in zip(("Avg Wait", "Avg TAT", "Throughput"), STAT_COLORS):
            var = tk.StringVar(value=f"{lbl}: —")
            self._stat_vars.append(var)
            tk.Label(sf, textvariable=var, bg=color, fg="white",
                     font=("Segoe UI", 9, "bold"),
                     padx=14, pady=5, relief="flat").pack(side="left", padx=(0, 4))

        # MLFQ extras
        if self.alg == "MLFQ":
            self._mlfq_vars = []
            for lbl, color in [("Demotions", "#ef4444"), ("Promotions", "#22c55e")]:
                var = tk.StringVar(value=f"{lbl}: —")
                self._mlfq_vars.append(var)
                tk.Label(sf, textvariable=var, bg=color, fg="white",
                         font=("Segoe UI", 9, "bold"),
                         padx=14, pady=5, relief="flat").pack(side="left", padx=(0, 4))
            leg = tk.Frame(sf, bg=BG)
            leg.pack(side="right", padx=4)
            for lvl in (1, 2, 3):
                tk.Label(leg, text=f"■ {MLFQ_LEVEL_LABELS[lvl]}",
                         bg=MLFQ_LEVEL_COLORS[lvl], fg="white",
                         font=("Segoe UI", 8, "bold"),
                         padx=6, pady=3).pack(side="left", padx=2)

        #  cache stats strip (NEW) ─
        cf = tk.Frame(self, bg=BG)
        cf.pack(fill="x", padx=8, pady=(0, 4))

        self._cache_vars = []
        cache_labels = ("Cache Hits", "Cache Misses", "Hit Rate")
        for lbl, color in zip(cache_labels, CACHE_STAT_COLORS):
            var = tk.StringVar(value=f"{lbl}: —")
            self._cache_vars.append(var)
            tk.Label(cf, textvariable=var, bg=color, fg="white",
                     font=("Segoe UI", 9, "bold"),
                     padx=14, pady=5, relief="flat").pack(side="left", padx=(0, 4))

        # ctx-switch legend
        leg2 = tk.Frame(cf, bg=BG)
        leg2.pack(side="right", padx=4)
        for txt, bg_c in [("■ CTX-Hit", CTX_HIT_COLOR), ("■ CTX-Miss", CTX_MISS_COLOR)]:
            tk.Label(leg2, text=txt, bg=bg_c, fg="white",
                     font=("Segoe UI", 8, "bold"),
                     padx=6, pady=3).pack(side="left", padx=2)

        #  Gantt chart 
        gf = tk.Frame(self, bg=PANEL_BG, highlightthickness=1,
                      highlightbackground=BORDER)
        gf.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tk.Label(gf, text="Gantt Chart  (dark bars = context-switch overhead)",
                 bg=PANEL_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 0))

        canv_wrap = tk.Frame(gf, bg=PANEL_BG)
        canv_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self._canvas = tk.Canvas(canv_wrap, bg=PANEL_BG, highlightthickness=0)
        hbar = ttk.Scrollbar(canv_wrap, orient="horizontal",
                             command=self._canvas.xview)
        vbar = ttk.Scrollbar(canv_wrap, orient="vertical",
                             command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right",  fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._canvas.bind("<MouseWheel>",
                          lambda e: self._canvas.yview_scroll(int(-e.delta / 60), "units"))
        self._canvas.bind("<Shift-MouseWheel>",
                          lambda e: self._canvas.xview_scroll(int(-e.delta / 60), "units"))

    #  public API 
    def clear(self):
        for row in self._rtree.get_children():
            self._rtree.delete(row)
        self._canvas.delete("all")
        for var, lbl in zip(self._stat_vars, ("Avg Wait", "Avg TAT", "Throughput")):
            var.set(f"{lbl}: —")
        for var, lbl in zip(self._cache_vars, ("Cache Hits", "Cache Misses", "Hit Rate")):
            var.set(f"{lbl}: —")
        if self.alg == "MLFQ":
            for var, lbl in zip(self._mlfq_vars, ("Demotions", "Promotions")):
                var.set(f"{lbl}: —")

    def update(self, procs, timeline, cache: CacheManager):
        self._fill_table(procs)
        self._fill_stats(procs, timeline)
        self._fill_cache_stats(cache)
        self._draw_gantt(procs, timeline)

    #  internals 
    def _fill_table(self, procs):
        for row in self._rtree.get_children():
            self._rtree.delete(row)
        tags = {1: "H", 2: "M", 3: "L"}
        for p in sorted(procs, key=lambda x: x.pid):
            self._rtree.insert("", "end", values=(
                p.pid, p.arrival, p.burst,
                PRIO_LABEL[p.priority],
                f"{p.memory}MB",
                f"{p.cache_required}u",   # cache working-set size
                p.start, p.finish, p.waiting, p.turnaround,
            ), tags=(tags[p.priority],))

    def _fill_stats(self, procs, timeline):
        n      = len(procs)
        avg_w  = sum(p.waiting    for p in procs) / n
        avg_tt = sum(p.turnaround for p in procs) / n
        cpu    = [e for e in timeline if e[0] == "cpu"]
        max_t  = max((e[3] for e in cpu), default=1)
        thru   = n / max_t if max_t else 0
        for var, lbl, val in zip(self._stat_vars,
                                  ("Avg Wait", "Avg TAT", "Throughput"),
                                  (f"{avg_w:.2f}", f"{avg_tt:.2f}", f"{thru:.3f} p/t")):
            var.set(f"{lbl}: {val}")

        if self.alg == "MLFQ":
            self._mlfq_vars[0].set(f"Demotions: {sum(1 for e in timeline if e[0]=='demote')}")
            self._mlfq_vars[1].set(f"Promotions: {sum(1 for e in timeline if e[0]=='promote')}")

    def _fill_cache_stats(self, cache: CacheManager):
        """Populate the cache stats strip with hit/miss/rate data."""
        self._cache_vars[0].set(f"Cache Hits: {cache.hits}")
        self._cache_vars[1].set(f"Cache Misses: {cache.misses}")
        self._cache_vars[2].set(f"Hit Rate: {cache.hit_rate:.1%}")

    def _draw_gantt(self, procs, timeline):
        self._canvas.delete("all")
        cpu = [e for e in timeline if e[0] == "cpu"]
        if not cpu:
            return

        ROW_H   = 30
        ROW_GAP = 5
        LEFT    = 54
        TOP     = 28
        PX      = 16

        # include ctx_switch events when computing max_t so the chart is wide enough
        ctx = [e for e in timeline if e[0] == "ctx_switch"]
        all_ends = [e[3] for e in cpu] + [e[3] for e in ctx]
        max_t = max(all_ends)

        if self.alg == "MLFQ":
            self._draw_gantt_mlfq(cpu, ctx, timeline, max_t,
                                  ROW_H, ROW_GAP, LEFT, TOP, PX)
        else:
            self._draw_gantt_standard(cpu, ctx, max_t,
                                      ROW_H, ROW_GAP, LEFT, TOP, PX)

    #  standard Gantt (per-process colours) 
    def _draw_gantt_standard(self, cpu, ctx, max_t,
                             ROW_H, ROW_GAP, LEFT, TOP, PX):
        palette = GANTT_PALETTE[self.alg]

        pid_first = {}
        for _, pid, t0, t1, _ in cpu:
            pid_first.setdefault(pid, t0)
        # also pick up pids that only appear in ctx (very short processes)
        for _, pid, t0, t1, _ in ctx:
            pid_first.setdefault(pid, t0)

        pids      = sorted(pid_first, key=pid_first.get)
        pid_row   = {pid: i for i, pid in enumerate(pids)}
        pid_color = {pid: palette[i % len(palette)]
                     for i, pid in enumerate(pids)}

        W = LEFT + max_t * PX + 24
        H = TOP  + len(pids) * (ROW_H + ROW_GAP) + 18
        self._canvas.configure(scrollregion=(0, 0, W, H))
        self._draw_grid(max_t, LEFT, TOP, H, PX)

        # row backgrounds + labels
        for pid, row in pid_row.items():
            y = TOP + row * (ROW_H + ROW_GAP)
            self._canvas.create_rectangle(LEFT, y, LEFT + max_t * PX, y + ROW_H,
                                          fill="#f8fafc", outline="")
            self._canvas.create_text(LEFT - 6, y + ROW_H // 2,
                                     text=pid, font=("Consolas", 9, "bold"),
                                     fill=TEXT_DARK, anchor="e")

        # ① draw context-switch overhead bars FIRST (drawn below CPU bars)
        for _, pid, t0, t1, is_hit in ctx:
            if pid not in pid_row:
                continue
            row   = pid_row[pid]
            x1    = LEFT + t0 * PX
            x2    = LEFT + t1 * PX
            y1    = TOP  + row * (ROW_H + ROW_GAP)
            y2    = y1 + ROW_H
            color = CTX_HIT_COLOR if is_hit else CTX_MISS_COLOR
            self._canvas.create_rectangle(x1 + 1, y1 + 2, x2 - 1, y2 - 2,
                                          fill=color, outline="")
            w = x2 - x1
            if w >= 12:
                label = "H" if is_hit else "M"
                self._canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                         text=label,
                                         font=("Consolas", 8, "bold"), fill="white")

        # ② draw CPU burst bars ON TOP
        for _, pid, t0, t1, _ in cpu:
            row   = pid_row[pid]
            x1    = LEFT + t0 * PX
            x2    = LEFT + t1 * PX
            y1    = TOP  + row * (ROW_H + ROW_GAP)
            y2    = y1 + ROW_H
            color = pid_color[pid]
            w     = x2 - x1
            self._canvas.create_rectangle(x1 + 1, y1 + 2, x2 - 1, y2 - 2,
                                          fill=color, outline="")
            if w >= 14:
                self._canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                         text=pid if w >= 26 else "·",
                                         font=("Consolas", 8, "bold"), fill="white")

        x = LEFT + max_t * PX
        self._canvas.create_text(x, TOP - 15, text=str(max_t),
                                  font=("Consolas", 7, "bold"),
                                  fill=TEXT_DARK, anchor="center")

    #  MLFQ Gantt (level colours + demotion / promotion markers) 
    def _draw_gantt_mlfq(self, cpu, ctx, timeline, max_t,
                         ROW_H, ROW_GAP, LEFT, TOP, PX):
        pid_first = {}
        for _, pid, t0, t1, _ in cpu:
            pid_first.setdefault(pid, t0)
        for _, pid, t0, t1, _ in ctx:
            pid_first.setdefault(pid, t0)

        pids    = sorted(pid_first, key=pid_first.get)
        pid_row = {pid: i for i, pid in enumerate(pids)}

        W = LEFT + max_t * PX + 24
        H = TOP  + len(pids) * (ROW_H + ROW_GAP) + 44
        self._canvas.configure(scrollregion=(0, 0, W, H))
        self._draw_grid(max_t, LEFT, TOP, H, PX)

        for pid, row in pid_row.items():
            y = TOP + row * (ROW_H + ROW_GAP)
            self._canvas.create_rectangle(LEFT, y, LEFT + max_t * PX, y + ROW_H,
                                          fill="#f8fafc", outline="")
            self._canvas.create_text(LEFT - 6, y + ROW_H // 2,
                                     text=pid, font=("Consolas", 9, "bold"),
                                     fill=TEXT_DARK, anchor="e")

        # ① context-switch overhead
        for _, pid, t0, t1, is_hit in ctx:
            if pid not in pid_row:
                continue
            row   = pid_row[pid]
            x1    = LEFT + t0 * PX
            x2    = LEFT + t1 * PX
            y1    = TOP  + row * (ROW_H + ROW_GAP)
            y2    = y1 + ROW_H
            color = CTX_HIT_COLOR if is_hit else CTX_MISS_COLOR
            self._canvas.create_rectangle(x1 + 1, y1 + 2, x2 - 1, y2 - 2,
                                          fill=color, outline="")
            w = x2 - x1
            if w >= 12:
                self._canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                         text="H" if is_hit else "M",
                                         font=("Consolas", 8, "bold"), fill="white")

        # ② CPU slices coloured by queue level
        for _, pid, t0, t1, lvl in cpu:
            row   = pid_row[pid]
            x1    = LEFT + t0 * PX
            x2    = LEFT + t1 * PX
            y1    = TOP  + row * (ROW_H + ROW_GAP)
            y2    = y1 + ROW_H
            color = MLFQ_LEVEL_COLORS[lvl]
            w     = x2 - x1
            self._canvas.create_rectangle(x1 + 1, y1 + 2, x2 - 1, y2 - 2,
                                          fill=color, outline="")
            if w >= 14:
                label = pid if w >= 26 else f"Q{lvl}"
                self._canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                         text=label,
                                         font=("Consolas", 8, "bold"), fill="white")

        # demotion / promotion markers
        for entry in timeline:
            if entry[0] not in ("demote", "promote"):
                continue
            kind, t, pid, old_lvl, new_lvl = entry
            if pid not in pid_row:
                continue
            row = pid_row[pid]
            x   = LEFT + t * PX
            y   = TOP  + row * (ROW_H + ROW_GAP)
            if kind == "demote":
                self._canvas.create_text(x, y - 2, text="▼",
                                         font=("Segoe UI", 9), fill="#dc2626", anchor="s")
            else:
                self._canvas.create_text(x, y + ROW_H + 2, text="▲",
                                         font=("Segoe UI", 9), fill="#16a34a", anchor="n")

        x = LEFT + max_t * PX
        self._canvas.create_text(x, TOP - 15, text=str(max_t),
                                  font=("Consolas", 7, "bold"),
                                  fill=TEXT_DARK, anchor="center")

    def _draw_grid(self, max_t, LEFT, TOP, H, PX):
        for t in range(0, max_t + 1):
            x     = LEFT + t * PX
            major = (t % 5 == 0)
            self._canvas.create_line(x, TOP - 6, x, H - 12,
                                     fill="#cbd5e1" if major else "#f1f5f9", width=1)
            if major:
                self._canvas.create_text(x, TOP - 15, text=str(t),
                                         font=("Consolas", 7),
                                         fill=TEXT_MID, anchor="center")


# ═
#  Main window
# ═
class SchedulerGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("OS Process Scheduler Simulator  (with Cache)")
        self.geometry("1380x860")
        self.minsize(980, 680)
        self.configure(bg=BG)

        self._setup_styles()
        self._seed_var = tk.IntVar(value=42)
        self._procs    = []

        self._build_header()
        self._build_body()
        self._regenerate()

        self.after(60, lambda: self._pane.sash_place(0, 290, 0))

    #  styles 
    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, font=("Segoe UI", 10))
        s.configure("Treeview",
                     rowheight=26, font=("Consolas", 9),
                     background=PANEL_BG, fieldbackground=PANEL_BG,
                     foreground=TEXT_DARK, borderwidth=0)
        s.configure("Treeview.Heading",
                     font=("Segoe UI", 9, "bold"),
                     background="#e2e8f0", foreground=TEXT_DARK, relief="flat")
        s.map("Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", "white")])
        s.configure("TNotebook", background=BG, tabmargins=[2, 6, 0, 0])
        s.configure("TNotebook.Tab",
                     padding=[18, 7], font=("Segoe UI", 10, "bold"),
                     background="#dde3eb", foreground=TEXT_MID)
        s.map("TNotebook.Tab",
              background=[("selected", PANEL_BG)],
              foreground=[("selected", ACCENT)])
        s.configure("TScrollbar",
                     troughcolor=BG, background="#cbd5e1", arrowcolor=TEXT_MID)

    #  header 
    def _build_header(self):
        hdr = tk.Frame(self, bg=HDR_BG)
        hdr.pack(fill="x")

        tk.Label(hdr, text="⚙  OS Process Scheduler Simulator  +  Cache",
                 bg=HDR_BG, fg="white",
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=18, pady=13)

        ctrl = tk.Frame(hdr, bg=HDR_BG)
        ctrl.pack(side="right", padx=14, pady=8)

        tk.Label(ctrl, text="Seed:", bg=HDR_BG, fg=TEXT_LIGHT,
                 font=("Segoe UI", 10)).pack(side="left")
        tk.Spinbox(ctrl, textvariable=self._seed_var, from_=0, to=9999,
                   width=6, font=("Segoe UI", 10),
                   bg="#334155", fg="white", insertbackground="white",
                   buttonbackground="#475569", relief="flat",
                   highlightthickness=0).pack(side="left", padx=(4, 12))

        for text, cmd, bg_c in [
            ("↺  Regenerate", self._regenerate, "#334155"),
            ("▶  Run All",    self._run_all,    ACCENT),
        ]:
            tk.Button(ctrl, text=text, command=cmd,
                      bg=bg_c, fg="white", font=("Segoe UI", 10, "bold"),
                      relief="flat", padx=14, pady=5,
                      activebackground=ACCENT_DARK, activeforeground="white",
                      cursor="hand2").pack(side="left", padx=3)

        sub = tk.Frame(self, bg=SUBHDR_BG)
        sub.pack(fill="x")
        for text in [
            f"RAM: {TOTAL_RAM_MB} MB  |  Cache: {CACHE_CAPACITY} units",
            f"CTX-switch: {CONTEXT_SWITCH_COST} tick  +  Miss penalty: {CACHE_MISS_PENALTY} ticks",
            f"Quantum: {QUANTUM}  |  MLFQ quanta: Q1={MLFQ_QUANTA[1]} Q2={MLFQ_QUANTA[2]} Q3={MLFQ_QUANTA[3]}",
            f"Sim Window: {SIM_TIME} ticks  |  Aging: {AGING_THRESHOLD} ticks",
            "  Gantt: dark slate=CTX-Hit  dark red=CTX-Miss",
        ]:
            tk.Label(sub, text=text, bg=SUBHDR_BG, fg="#64748b",
                     font=("Segoe UI", 8)).pack(side="left", padx=10, pady=3)

    #  body 
    def _build_body(self):
        self._pane = tk.PanedWindow(self, orient="horizontal",
                                    bg=BG, sashwidth=6,
                                    sashrelief="flat", sashpad=0)
        self._pane.pack(fill="both", expand=True)

        # left: process list
        left = tk.Frame(self._pane, bg=PANEL_BG)
        self._pane.add(left, minsize=210, width=290)

        tk.Label(left, text="Process List", bg=PANEL_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        tk.Label(left, text="Rows coloured by priority · Cache = working-set size",
                 bg=PANEL_BG, fg=TEXT_LIGHT,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 6))

        tv_wrap = tk.Frame(left, bg=PANEL_BG)
        tv_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        cols   = ("pid", "arr", "bst", "pri", "mem", "cache")
        hdrs   = ("PID", "Arr", "Burst", "Prio", "Mem", "Cache")
        widths = (40, 36, 44, 42, 52, 46)

        self._ptree = ttk.Treeview(tv_wrap, columns=cols,
                                   show="headings", selectmode="none")
        for c, h, w in zip(cols, hdrs, widths):
            self._ptree.heading(c, text=h)
            self._ptree.column(c, width=w, minwidth=w, anchor="center", stretch=False)

        psb = ttk.Scrollbar(tv_wrap, orient="vertical", command=self._ptree.yview)
        self._ptree.configure(yscrollcommand=psb.set)
        self._ptree.pack(side="left", fill="both", expand=True)
        psb.pack(side="right", fill="y")

        self._ptree.tag_configure("H", background="#fff1f2", foreground="#9f1239")
        self._ptree.tag_configure("M", background="#fffbeb", foreground="#92400e")
        self._ptree.tag_configure("L", background="#f0fdf4", foreground="#166534")

        leg = tk.Frame(left, bg=PANEL_BG)
        leg.pack(fill="x", padx=8, pady=(0, 10))
        for lbl, bg_c, fg_c in [
            ("■ High", "#fff1f2", "#9f1239"),
            ("■ Med",  "#fffbeb", "#92400e"),
            ("■ Low",  "#f0fdf4", "#166534"),
        ]:
            tk.Label(leg, text=lbl, bg=bg_c, fg=fg_c,
                     font=("Segoe UI", 8, "bold"),
                     padx=6, pady=3).pack(side="left", padx=2)

        # right: notebook
        right = tk.Frame(self._pane, bg=BG)
        self._pane.add(right, minsize=500)

        self._nb = ttk.Notebook(right)
        self._nb.pack(fill="both", expand=True, padx=4, pady=4)

        self._tabs = {}
        for alg, label in [
            ("FCFS",    "  FCFS  "),
            ("SJF",     "  SJF  "),
            ("SRTF",    "  SRTF  "),
            ("PRIO_NP", "  Priority (NP)  "),
            ("PRIO_P",  "  Priority (P)  "),
            ("RR",      f"  Round Robin (q={QUANTUM})  "),
            ("MLQ",     "  MLQ  "),
            ("MLFQ",    "  MLFQ  "),
        ]:
            tab = AlgorithmTab(self._nb, alg)
            self._nb.add(tab, text=label)
            self._tabs[alg] = tab

    #  actions 
    def _regenerate(self):
        random.seed(self._seed_var.get())
        self._procs = generate_processes(SIM_TIME)
        self._populate_ptree()
        for tab in self._tabs.values():
            tab.clear()

    def _populate_ptree(self):
        for row in self._ptree.get_children():
            self._ptree.delete(row)
        for p in self._procs:
            tag = {1: "H", 2: "M", 3: "L"}[p.priority]
            self._ptree.insert("", "end", values=(
                p.pid, p.arrival, p.burst,
                PRIO_LABEL[p.priority], f"{p.memory}MB", f"{p.cache_required}u",
            ), tags=(tag,))

    def _run_all(self):
        results = {}
        for alg in ("FCFS", "SJF", "SRTF", "PRIO_NP", "PRIO_P", "RR", "MLQ", "MLFQ"):
            procs, timeline, cache = run_algorithm(alg, self._procs)
            self._tabs[alg].update(procs, timeline, cache)

            n      = len(procs)
            avg_w  = sum(p.waiting    for p in procs) / n
            avg_tt = sum(p.turnaround for p in procs) / n
            cpu    = [e for e in timeline if e[0] == "cpu"]
            max_t  = max((e[3] for e in cpu), default=1)
            ctx    = [e for e in timeline if e[0] == "ctx_switch"]

            results[alg] = {
                "avg_w":      avg_w,
                "avg_tt":     avg_tt,
                "throughput": n / max_t if max_t else 0,
                "hits":       cache.hits,
                "misses":     cache.misses,
                "hit_rate":   cache.hit_rate,
                "ctx_switches": len(ctx),
            }

        self._print_comparison(results)

    def _print_comparison(self, results):
        print("\n" + "═" * 74)
        print("  ALGORITHM COMPARISON  (scheduler metrics + cache performance)")
        print("═" * 74)
        print(f"{'ALG':10} │ {'Avg-WT':>7} {'Avg-TAT':>8} {'Thru':>8} │"
              f" {'CTX':>5} {'Hits':>6} {'Misses':>7} {'HitRate':>8}")
        print("─" * 74)
        for alg, d in results.items():
            print(f"{alg:10} │ {d['avg_w']:>7.2f} {d['avg_tt']:>8.2f} "
                  f"{d['throughput']:>8.3f} │ "
                  f"{d['ctx_switches']:>5} {d['hits']:>6} {d['misses']:>7} "
                  f"{d['hit_rate']:>7.1%}")
        print("─" * 74)

        bests = {
            "Lowest Waiting Time":    min(results, key=lambda x: results[x]["avg_w"]),
            "Lowest Turnaround Time": min(results, key=lambda x: results[x]["avg_tt"]),
            "Highest Throughput":     max(results, key=lambda x: results[x]["throughput"]),
            "Highest Cache Hit Rate": max(results, key=lambda x: results[x]["hit_rate"]),
            "Fewest Context Switches":min(results, key=lambda x: results[x]["ctx_switches"]),
        }
        print()
        for metric, winner in bests.items():
            print(f"  {metric:28s} → {winner}")
        print("═" * 74 + "\n")


if __name__ == "__main__":
    app = SchedulerGUI()
    app.mainloop()