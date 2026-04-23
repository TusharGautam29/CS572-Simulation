import simpy
import random
import tkinter as tk
from tkinter import ttk
from copy import deepcopy
from config import *
from gui import SchedulerGUI

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
    def __init__(self, pid, arrival, burst, priority, memory):
        self.pid = pid
        self.arrival = arrival
        self.burst = burst
        self.priority = priority
        self.memory = memory
        self.start = -1
        self.finish = 0
        self.waiting = 0
        self.turnaround = 0


def generate_processes(sim_time):
    procs, t, pid = [], 0, 1
    while t < sim_time:
        t += random.randint(1, 5)
        if t > sim_time:
            break
        procs.append(Process(
            pid=f"P{pid}", arrival=t,
            burst=random.randint(2, 8),
            priority=random.randint(1, 3),
            memory=random.randint(16, 64),
        ))
        pid += 1
    return procs

def run_algorithm(alg, processes_orig):
    from algorithms import fcfs, sjf, srtf, priority_np, priority_p, round_robin, mlq, mlfq
    procs    = deepcopy(processes_orig)
    timeline = []
    env      = simpy.Environment()
    ram      = RAM(env, TOTAL_RAM_MB)
    if alg == "FCFS":
        env.process(fcfs(env, procs, ram, timeline))
    elif alg == "SJF":
        env.process(sjf(env, procs, ram, timeline))
    elif alg == "SRTF":
        env.process(srtf(env, procs, ram, timeline))
    elif alg == "PRIO_NP":
        env.process(priority_np(env, procs, ram, timeline))
    elif alg == "PRIO_P":
        env.process(priority_p(env, procs, ram, timeline))
    elif alg == "RR":
        env.process(round_robin(env, procs, ram, QUANTUM, timeline))
    elif alg == "MLQ":
        env.process(mlq(env, procs, ram, QUANTUM, timeline))
    elif alg == "MLFQ":
        env.process(mlfq(env, procs, ram, timeline))
    env.run()
    return procs, timeline        

if __name__ == "__main__":
    app = SchedulerGUI(generate_processes, run_algorithm)
    app.mainloop()