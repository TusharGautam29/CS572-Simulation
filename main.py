import simpy
import random
import tkinter as tk
from tkinter import ttk
from copy import deepcopy
from config import *
from gui import SchedulerGUI
from collections import OrderedDict

class CacheManager:
    

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




def run_algorithm(alg, processes_orig):
    from algorithms import fcfs, sjf, srtf, priority_np, priority_p, round_robin, mlq, mlfq
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
    return procs, timeline, cache        

if __name__ == "__main__":
    app = SchedulerGUI(generate_processes, run_algorithm)
    app.mainloop()