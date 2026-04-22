import simpy
import random
import tkinter as tk
from tkinter import ttk
from copy import deepcopy

# ───────────────────────────────────── simulation constants ───────────────────
TOTAL_RAM_MB     = 128
SIM_TIME         = 50
QUANTUM          = 3
MLFQ_QUANTA      = {1: 2, 2: 4, 3: 8}   # each level gets a longer quantum
AGING_THRESHOLD  = 12                    # ticks without CPU → promote one level

# ───────────────────────────────────── colour tokens ──────────────────────────
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

PRIO_LABEL  = {1: "High", 2: "Med", 3: "Low"}

GANTT_PALETTE = {
    "FCFS": ["#3b82f6","#2563eb","#1d4ed8","#0ea5e9","#0284c7",
             "#60a5fa","#0369a1","#38bdf8","#075985","#7dd3fc"],
    "SJF":  ["#f59e0b","#d97706","#b45309","#fbbf24","#92400e",
             "#fcd34d","#78350f","#fde68a","#a16207","#fed7aa"],    
    "RR":   ["#a855f7","#9333ea","#7e22ce","#c084fc","#6d28d9",
             "#8b5cf6","#581c87","#7c3aed","#4c1d95","#a78bfa"],
    "MLQ":  ["#10b981","#059669","#047857","#0d9488","#0f766e",
             "#34d399","#065f46","#14b8a6","#115e59","#2dd4bf"],
    # MLFQ colours are per-level, not per-process (handled specially in _draw_gantt)
    "MLFQ": ["#ef4444","#f97316","#94a3b8"],   # lvl1=red, lvl2=orange, lvl3=slate
}
GANTT_PALETTE["SRTF"] = GANTT_PALETTE["SJF"]
GANTT_PALETTE["PRIO_NP"] = GANTT_PALETTE["MLQ"]
GANTT_PALETTE["PRIO_P"]  = GANTT_PALETTE["MLQ"]

# MLFQ level labels used in the Gantt legend
MLFQ_LEVEL_COLORS = {1: "#ef4444", 2: "#f97316", 3: "#94a3b8"}
MLFQ_LEVEL_LABELS = {1: "Q1 (τ=2)", 2: "Q2 (τ=4)", 3: "Q3 (τ=8)"}

STAT_COLORS = ["#3b82f6", "#8b5cf6", "#10b981"]


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


# notifier 
def _make_notifier(env):
    """
    wake-up mechanism for the scheduler coroutine.

    HOW IT WORKS
    
    SimPy schedulers often need to *sleep* when the ready queue is empty
    and *wake up* the moment something new arrives.  A plain
    ``yield env.timeout(0)`` just burns a tick; a ``yield env.event()``
    sleeps forever unless someone explicitly succeeds it.

    _make_notifier() creates exactly that pair:

        wakeup_ref   – a one-element list that always holds the *current*
                       unarmed event the scheduler will yield on.
                       A list is used (instead of a plain variable) so the
                       inner closure can mutate the reference.

        notify()     – called by arrival coroutines and post-free paths.
                       It succeeds the event only if it has not fired yet
                       (safe to call multiple times per tick).

    SCHEDULER LOOP PATTERN
    ──────────────────────
        while not done:
            if ready_queue is empty:
                yield wakeup_ref[0]      # ← sleep here
                wakeup_ref[0] = env.event()   # re-arm for next sleep
                continue
            ... run next process ...
            notify()   # ← wake self in case new jobs appeared during run

    WHY NOT JUST poll with timeout(1)?
    ────────────────────────────────────
    Polling wastes sim ticks and produces incorrect timing.  The notifier
    pattern guarantees the scheduler wakes up *at the exact SimPy tick*
    when a process enters the ready queue, keeping statistics accurate.
    """
    ref = [env.event()]

    def notify():
        if not ref[0].triggered:
            ref[0].succeed()

    return ref, notify


# arrival coroutine 
def _arrival(env, p, ram, on_ready, notify, timeline):
    yield env.timeout(max(0, p.arrival - env.now))
    yield ram.allocate(p.memory)            # blocks until RAM is free
    timeline.append(("ram", env.now, p.pid, p.memory))
    on_ready(p)
    notify()


#  FCFS
def fcfs(env, processes, ram, timeline):
    ready = []
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))
    done = 0
    while done < len(processes):
        if not ready:
            yield wakeup[0]; wakeup[0] = env.event(); continue
        p = ready.pop(0)
        p.start = env.now
        t0 = env.now
        yield env.timeout(p.burst)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        p.finish     = env.now
        p.waiting    = p.start - p.arrival
        p.turnaround = p.finish - p.arrival
        done += 1
        yield ram.free(p.memory); yield env.timeout(0); notify()


# ─── SJF ─────────────────────────────────────────────────────────────────────
def sjf(env, processes, ram, timeline):
    ready = []
    wakeup, notify = _make_notifier(env)

    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done = 0
    while done < len(processes):
        if not ready:
            yield wakeup[0]
            wakeup[0] = env.event()
            continue

        ready.sort(key=lambda x: x.burst)
        p = ready.pop(0)

        p.start = env.now
        t0 = env.now

        yield env.timeout(p.burst)

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        p.finish     = env.now
        p.waiting    = p.start - p.arrival
        p.turnaround = p.finish - p.arrival

        done += 1

        yield ram.free(p.memory)
        yield env.timeout(0)
        notify()

def srtf(env, processes, ram, timeline):    # shortest remaining time first(premtive of sjf)
    ready = []
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)

    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done = 0
    current = None

    while done < len(processes):
        if not ready and current is None:
            yield wakeup[0]
            wakeup[0] = env.event()
            continue

        # pick shortest remaining job
        if current:
            ready.append(current)

        ready.sort(key=lambda x: remaining[x.pid])
        p = ready.pop(0)

        if p.start == -1:
            p.start = env.now

        current = p
        t0 = env.now

        # run for 1 unit (preemption point)
        yield env.timeout(1)
        remaining[p.pid] -= 1

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done += 1
            current = None
            yield ram.free(p.memory)
            yield env.timeout(0)

        notify()

def priority_np(env, processes, ram, timeline):
    ready = []
    wakeup, notify = _make_notifier(env)

    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done = 0
    while done < len(processes):
        if not ready:
            yield wakeup[0]
            wakeup[0] = env.event()
            continue

        # lower number = higher priority
        ready.sort(key=lambda x: x.priority)
        p = ready.pop(0)

        p.start = env.now
        t0 = env.now

        yield env.timeout(p.burst)

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        p.finish     = env.now
        p.waiting    = p.start - p.arrival
        p.turnaround = p.finish - p.arrival

        done += 1
        yield ram.free(p.memory)
        yield env.timeout(0)
        notify()

def priority_p(env, processes, ram, timeline):
    ready = []
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)

    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))

    done = 0
    current = None

    while done < len(processes):
        if not ready and current is None:
            yield wakeup[0]
            wakeup[0] = env.event()
            continue

        if current:
            ready.append(current)

        # preempt based on priority
        ready.sort(key=lambda x: x.priority)
        p = ready.pop(0)

        if p.start == -1:
            p.start = env.now

        current = p
        t0 = env.now

        yield env.timeout(1)
        remaining[p.pid] -= 1

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done += 1
            current = None
            yield ram.free(p.memory)
            yield env.timeout(0)

        notify()

# ─── Round Robin ─────────────────────────────────────────────────────────────
def round_robin(env, processes, ram, quantum, timeline):
    ready     = []
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)
    for p in processes:
        env.process(_arrival(env, p, ram, ready.append, notify, timeline))
    done = 0
    while done < len(processes):
        if not ready:
            yield wakeup[0]; wakeup[0] = env.event(); continue
        p = ready.pop(0)
        if p.start == -1:
            p.start = env.now
        run = min(quantum, remaining[p.pid])
        t0  = env.now
        yield env.timeout(run)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        remaining[p.pid] -= run
        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done += 1
            yield ram.free(p.memory); yield env.timeout(0)
        else:
            ready.append(p)
        notify()


# ─── MLQ ─────────────────────────────────────────────────────────────────────
def mlq(env, processes, ram, quantum, timeline):
    queues    = {1: [], 2: [], 3: []}
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)

    def on_ready(p): queues[p.priority].append(p)

    for p in processes:
        env.process(_arrival(env, p, ram, on_ready, notify, timeline))

    done = 0
    while done < len(processes):
        prio = next((lvl for lvl in (1, 2, 3) if queues[lvl]), None)
        if prio is None:
            yield wakeup[0]; wakeup[0] = env.event(); continue
        p = queues[prio].pop(0)
        if p.start == -1:
            p.start = env.now
        run = min(quantum, remaining[p.pid]) if prio == 2 else remaining[p.pid]
        t0  = env.now
        yield env.timeout(run)
        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        remaining[p.pid] -= run
        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done += 1
            yield ram.free(p.memory); yield env.timeout(0)
        else:
            queues[prio].append(p)
        notify()


# ─── MLFQ ────────────────────────────────────────────────────────────────────
def mlfq(env, processes, ram, timeline):
    """
    Multi-Level Feedback Queue with dynamic priority.

    Three queues, each with a progressively longer quantum:
        Level 1  τ=2   (highest priority)
        Level 2  τ=4
        Level 3  τ=8   (lowest priority, longest quantum)

    DEMOTION  – if a process uses its full quantum it moves DOWN one level.
    AGING     – after every CPU slice we scan waiting processes; any process
                that has not received CPU for ≥ AGING_THRESHOLD ticks is
                promoted UP one level (prevents starvation).

    The 5th field of every "cpu" timeline entry carries the *queue level*
    (not the original process priority) so the Gantt chart can colour each
    slice by the level it ran in.
    """
    queues    = {1: [], 2: [], 3: []}
    levels    = {}          # pid → current queue level
    last_cpu  = {}          # pid → sim-time of last CPU slice end (or arrival)
    remaining = {p.pid: p.burst for p in processes}
    wakeup, notify = _make_notifier(env)

    def on_ready(p):
        queues[1].append(p)
        levels[p.pid]   = 1
        last_cpu[p.pid] = env.now   # treat arrival as "last seen"

    for p in processes:
        env.process(_arrival(env, p, ram, on_ready, notify, timeline))

    done = 0
    while done < len(processes):

        # ── aging pass: promote starving processes ─────────────────────────
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

        # ── pick highest-priority non-empty queue ──────────────────────────
        prio = next((lvl for lvl in (1, 2, 3) if queues[lvl]), None)
        if prio is None:
            yield wakeup[0]; wakeup[0] = env.event(); continue

        p       = queues[prio].pop(0)
        quantum = MLFQ_QUANTA[prio]
        run     = min(quantum, remaining[p.pid])

        if p.start == -1:
            p.start = env.now

        t0 = env.now
        yield env.timeout(run)

        # store queue LEVEL (not original priority) for colour-coding
        timeline.append(("cpu", p.pid, t0, env.now, prio))

        last_cpu[p.pid]  = env.now
        remaining[p.pid] -= run

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done += 1
            yield ram.free(p.memory); yield env.timeout(0)
        else:
            # demote if we used the full quantum; otherwise re-queue same level
            if run == quantum:
                new_lvl = min(prio + 1, 3)
                levels[p.pid] = new_lvl
                queues[new_lvl].append(p)
                timeline.append(("demote", env.now, p.pid, prio, new_lvl))
            else:
                queues[prio].append(p)

        notify()

def run_algorithm(alg, processes_orig):
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


# ═══════════════════════════════════ GUI ══════════════════════════════════════

class AlgorithmTab(tk.Frame):
    """One notebook tab: results table + stats strip + Gantt canvas."""

    def __init__(self, parent, alg):
        super().__init__(parent, bg=BG)
        self.alg = alg
        self._build()

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # ── results table ─────────────────────────────────────────────────
        rf = tk.Frame(self, bg=PANEL_BG, highlightthickness=1,
                      highlightbackground=BORDER)
        rf.pack(fill="x", padx=8, pady=(8, 3))

        tk.Label(rf, text="Results", bg=PANEL_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 3))

        tree_wrap = tk.Frame(rf, bg=PANEL_BG)
        tree_wrap.pack(fill="x", padx=6, pady=(0, 6))

        cols   = ("pid", "arr", "bst", "pri", "mem", "start", "fin", "wait", "tat")
        hdrs   = ("PID", "Arr", "Burst", "Prio", "Mem",
                  "Start", "Finish", "Wait", "TAT")
        widths = (44, 40, 48, 48, 54, 50, 54, 46, 46)

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

        # ── stats strip ────────────────────────────────────────────────────
        sf = tk.Frame(self, bg=BG)
        sf.pack(fill="x", padx=8, pady=(0, 4))

        self._stat_vars = []
        labels = ("Avg Wait", "Avg TAT", "Throughput")
        for lbl, color in zip(labels, STAT_COLORS):
            var = tk.StringVar(value=f"{lbl}: —")
            self._stat_vars.append(var)
            tk.Label(sf, textvariable=var,
                     bg=color, fg="white",
                     font=("Segoe UI", 9, "bold"),
                     padx=14, pady=6, relief="flat").pack(side="left", padx=(0, 4))

        # MLFQ-specific: demotion / promotion counters
        if self.alg == "MLFQ":
            self._mlfq_vars = []
            for lbl, color in [("Demotions", "#ef4444"), ("Promotions", "#22c55e")]:
                var = tk.StringVar(value=f"{lbl}: —")
                self._mlfq_vars.append(var)
                tk.Label(sf, textvariable=var,
                         bg=color, fg="white",
                         font=("Segoe UI", 9, "bold"),
                         padx=14, pady=6, relief="flat").pack(side="left", padx=(0, 4))

            # level legend
            leg = tk.Frame(sf, bg=BG)
            leg.pack(side="right", padx=4)
            for lvl in (1, 2, 3):
                tk.Label(leg, text=f"■ {MLFQ_LEVEL_LABELS[lvl]}",
                         bg=MLFQ_LEVEL_COLORS[lvl], fg="white",
                         font=("Segoe UI", 8, "bold"),
                         padx=6, pady=3).pack(side="left", padx=2)

        # ── Gantt chart ────────────────────────────────────────────────────
        gf = tk.Frame(self, bg=PANEL_BG, highlightthickness=1,
                      highlightbackground=BORDER)
        gf.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tk.Label(gf, text="Gantt Chart", bg=PANEL_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 0))

        canv_wrap = tk.Frame(gf, bg=PANEL_BG)
        canv_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self._canvas = tk.Canvas(canv_wrap, bg=PANEL_BG, highlightthickness=0)
        hbar = ttk.Scrollbar(canv_wrap, orient="horizontal",
                             command=self._canvas.xview)
        vbar = ttk.Scrollbar(canv_wrap, orient="vertical",
                             command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=hbar.set,
                               yscrollcommand=vbar.set)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right",  fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        # bind mousewheel
        self._canvas.bind("<MouseWheel>",
                          lambda e: self._canvas.yview_scroll(
                              int(-e.delta / 60), "units"))
        self._canvas.bind("<Shift-MouseWheel>",
                          lambda e: self._canvas.xview_scroll(
                              int(-e.delta / 60), "units"))

    # ── public API ────────────────────────────────────────────────────────────
    def clear(self):
        for row in self._rtree.get_children():
            self._rtree.delete(row)
        self._canvas.delete("all")
        labels = ("Avg Wait", "Avg TAT", "Throughput")
        for var, lbl in zip(self._stat_vars, labels):
            var.set(f"{lbl}: —")
        if self.alg == "MLFQ":
            for var, lbl in zip(self._mlfq_vars, ("Demotions", "Promotions")):
                var.set(f"{lbl}: —")

    def update(self, procs, timeline):
        self._fill_table(procs)
        self._fill_stats(procs, timeline)
        self._draw_gantt(procs, timeline)

    # ── internals ─────────────────────────────────────────────────────────────
    def _fill_table(self, procs):
        for row in self._rtree.get_children():
            self._rtree.delete(row)
        tags = {1: "H", 2: "M", 3: "L"}
        for p in sorted(procs, key=lambda x: x.pid):
            self._rtree.insert("", "end", values=(
                p.pid, p.arrival, p.burst,
                PRIO_LABEL[p.priority],
                f"{p.memory}MB",
                p.start, p.finish, p.waiting, p.turnaround,
            ), tags=(tags[p.priority],))

    def _fill_stats(self, procs, timeline):
        n      = len(procs)
        avg_w  = sum(p.waiting    for p in procs) / n
        avg_tt = sum(p.turnaround for p in procs) / n
        cpu    = [e for e in timeline if e[0] == "cpu"]
        max_t  = max((e[3] for e in cpu), default=1)
        thru   = n / max_t if max_t else 0
        values = (f"{avg_w:.2f}", f"{avg_tt:.2f}", f"{thru:.3f} p/t")
        labels = ("Avg Wait", "Avg TAT", "Throughput")
        for var, lbl, val in zip(self._stat_vars, labels, values):
            var.set(f"{lbl}: {val}")

        if self.alg == "MLFQ":
            demotions  = sum(1 for e in timeline if e[0] == "demote")
            promotions = sum(1 for e in timeline if e[0] == "promote")
            self._mlfq_vars[0].set(f"Demotions: {demotions}")
            self._mlfq_vars[1].set(f"Promotions: {promotions}")

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

        max_t = max(e[3] for e in cpu)
        if self.alg == "MLFQ":
            self._draw_gantt_mlfq(cpu, timeline, max_t,
                                  ROW_H, ROW_GAP, LEFT, TOP, PX)
        else:
            self._draw_gantt_standard(cpu, max_t,
                                      ROW_H, ROW_GAP, LEFT, TOP, PX)

    def _draw_gantt_standard(self, cpu, max_t, ROW_H, ROW_GAP, LEFT, TOP, PX):
        """Original per-process colour Gantt used by FCFS / SJF / RR / MLQ."""
        palette = GANTT_PALETTE[self.alg]

        pid_first = {}
        for _, pid, t0, t1, prio in cpu:
            pid_first.setdefault(pid, t0)
        pids      = sorted(pid_first, key=pid_first.get)
        pid_row   = {pid: i for i, pid in enumerate(pids)}
        pid_color = {pid: palette[i % len(palette)]
                     for i, pid in enumerate(pids)}

        W = LEFT + max_t * PX + 24
        H = TOP  + len(pids) * (ROW_H + ROW_GAP) + 18
        self._canvas.configure(scrollregion=(0, 0, W, H))

        self._draw_grid(max_t, LEFT, TOP, H, PX)

        for pid, row in pid_row.items():
            y = TOP + row * (ROW_H + ROW_GAP)
            self._canvas.create_rectangle(LEFT, y, LEFT + max_t * PX, y + ROW_H,
                                          fill="#f8fafc", outline="")
            self._canvas.create_text(LEFT - 6, y + ROW_H // 2,
                                     text=pid, font=("Consolas", 9, "bold"),
                                     fill=TEXT_DARK, anchor="e")

        for _, pid, t0, t1, prio in cpu:
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

    def _draw_gantt_mlfq(self, cpu, timeline, max_t,
                         ROW_H, ROW_GAP, LEFT, TOP, PX):
        """
        MLFQ Gantt: one row per process, slices coloured by *queue level*.
        Demotion  arrows (▼) and promotion stars (★) are drawn at the
        right edge of the slice where the transition occurred.
        """
        pid_first = {}
        for _, pid, t0, t1, lvl in cpu:
            pid_first.setdefault(pid, t0)
        pids    = sorted(pid_first, key=pid_first.get)
        pid_row = {pid: i for i, pid in enumerate(pids)}

        W = LEFT + max_t * PX + 24
        H = TOP  + len(pids) * (ROW_H + ROW_GAP) + 44   # extra for legend
        self._canvas.configure(scrollregion=(0, 0, W, H))

        self._draw_grid(max_t, LEFT, TOP, H, PX)

        for pid, row in pid_row.items():
            y = TOP + row * (ROW_H + ROW_GAP)
            self._canvas.create_rectangle(LEFT, y, LEFT + max_t * PX, y + ROW_H,
                                          fill="#f8fafc", outline="")
            self._canvas.create_text(LEFT - 6, y + ROW_H // 2,
                                     text=pid, font=("Consolas", 9, "bold"),
                                     fill=TEXT_DARK, anchor="e")

        # draw CPU slices coloured by queue level
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
                sym, col = "▼", "#dc2626"
                self._canvas.create_text(x, y - 2, text=sym,
                                         font=("Segoe UI", 9), fill=col,
                                         anchor="s")
            else:
                sym, col = "▲", "#16a34a"
                self._canvas.create_text(x, y + ROW_H + 2, text=sym,
                                         font=("Segoe UI", 9), fill=col,
                                         anchor="n")

        # end-of-time marker
        x = LEFT + max_t * PX
        self._canvas.create_text(x, TOP - 15, text=str(max_t),
                                  font=("Consolas", 7, "bold"),
                                  fill=TEXT_DARK, anchor="center")

    def _draw_grid(self, max_t, LEFT, TOP, H, PX):
        for t in range(0, max_t + 1):
            x     = LEFT + t * PX
            major = (t % 5 == 0)
            self._canvas.create_line(x, TOP - 6, x, H - 12,
                                     fill="#cbd5e1" if major else "#f1f5f9",
                                     width=1)
            if major:
                self._canvas.create_text(x, TOP - 15, text=str(t),
                                         font=("Consolas", 7),
                                         fill=TEXT_MID, anchor="center")


class SchedulerGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("OS Process Scheduler Simulator")
        self.geometry("1320x840")
        self.minsize(960, 660)
        self.configure(bg=BG)

        self._setup_styles()
        self._seed_var = tk.IntVar(value=42)
        self._procs    = []

        self._build_header()
        self._build_body()
        self._regenerate()

        self.after(60, lambda: self._pane.sash_place(0, 270, 0))

    # ── ttk styles ────────────────────────────────────────────────────────────
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
                     background="#e2e8f0", foreground=TEXT_DARK,
                     relief="flat")
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
                     troughcolor=BG, background="#cbd5e1",
                     arrowcolor=TEXT_MID)

    # ── header ────────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self, bg=HDR_BG)
        hdr.pack(fill="x")

        tk.Label(hdr, text="⚙  OS Process Scheduler Simulator",
                 bg=HDR_BG, fg="white",
                 font=("Segoe UI", 13, "bold")).pack(
                     side="left", padx=18, pady=13)

        ctrl = tk.Frame(hdr, bg=HDR_BG)
        ctrl.pack(side="right", padx=14, pady=8)

        tk.Label(ctrl, text="Seed:", bg=HDR_BG, fg=TEXT_LIGHT,
                 font=("Segoe UI", 10)).pack(side="left")

        tk.Spinbox(ctrl, textvariable=self._seed_var, from_=0, to=9999,
                   width=6, font=("Segoe UI", 10),
                   bg="#334155", fg="white",
                   insertbackground="white",
                   buttonbackground="#475569",
                   relief="flat",
                   highlightthickness=0).pack(side="left", padx=(4, 12))

        for text, cmd, bg_c in [
            ("↺  Regenerate", self._regenerate, "#334155"),
            ("▶  Run All",    self._run_all,    ACCENT),
        ]:
            tk.Button(ctrl, text=text, command=cmd,
                      bg=bg_c, fg="white",
                      font=("Segoe UI", 10, "bold"),
                      relief="flat", padx=14, pady=5,
                      activebackground=ACCENT_DARK,
                      activeforeground="white",
                      cursor="hand2").pack(side="left", padx=3)

        sub = tk.Frame(self, bg=SUBHDR_BG)
        sub.pack(fill="x")
        for text in [
            f"RAM: {TOTAL_RAM_MB} MB",
            f"Quantum: {QUANTUM}  |  MLFQ quanta: Q1={MLFQ_QUANTA[1]} Q2={MLFQ_QUANTA[2]} Q3={MLFQ_QUANTA[3]}",
            f"Sim Window: {SIM_TIME} ticks  |  Aging threshold: {AGING_THRESHOLD} ticks",
            "  Colour →  Red = High · Amber = Med · Green = Low",
            "  MLFQ →  ▼ demotion  ▲ promotion (aging)",
        ]:
            tk.Label(sub, text=text, bg=SUBHDR_BG, fg="#64748b",
                     font=("Segoe UI", 8)).pack(side="left", padx=12, pady=3)

    # ── body ──────────────────────────────────────────────────────────────────
    def _build_body(self):
        self._pane = tk.PanedWindow(self, orient="horizontal",
                                    bg=BG, sashwidth=6,
                                    sashrelief="flat", sashpad=0)
        self._pane.pack(fill="both", expand=True)

        # ── left: process list ─────────────────────────────────────────────
        left = tk.Frame(self._pane, bg=PANEL_BG)
        self._pane.add(left, minsize=200, width=270)

        tk.Label(left, text="Process List", bg=PANEL_BG, fg=TEXT_DARK,
                 font=("Segoe UI", 11, "bold")).pack(
                     anchor="w", padx=12, pady=(12, 2))

        tk.Label(left, text="Rows coloured by priority",
                 bg=PANEL_BG, fg=TEXT_LIGHT,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 6))

        tv_wrap = tk.Frame(left, bg=PANEL_BG)
        tv_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        cols   = ("pid", "arr", "bst", "pri", "mem")
        hdrs   = ("PID", "Arr", "Burst", "Prio", "Mem")
        widths = (40, 36, 44, 42, 54)

        self._ptree = ttk.Treeview(tv_wrap, columns=cols,
                                   show="headings", selectmode="none")
        for c, h, w in zip(cols, hdrs, widths):
            self._ptree.heading(c, text=h)
            self._ptree.column(c, width=w, minwidth=w,
                               anchor="center", stretch=False)

        psb = ttk.Scrollbar(tv_wrap, orient="vertical",
                            command=self._ptree.yview)
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

        # ── right: notebook ────────────────────────────────────────────────
        right = tk.Frame(self._pane, bg=BG)
        self._pane.add(right, minsize=500)

        self._nb = ttk.Notebook(right)
        self._nb.pack(fill="both", expand=True, padx=4, pady=4)

        self._tabs = {}
        for alg, label in [
            ("FCFS", "  FCFS  "),
            ("SJF",  "  SJF  "),
            ("SRTF",  "  SRTF  "),
            ("PRIO_NP", "  Priority (NP)  "),
            ("PRIO_P",  "  Priority (P)  "),
            ("RR",   f"  Round Robin  (q={QUANTUM})  "),
            ("MLQ",  "  MLQ  "),
            ("MLFQ", "  MLFQ  "),
        ]:
            tab = AlgorithmTab(self._nb, alg)
            self._nb.add(tab, text=label)
            self._tabs[alg] = tab

    # ── actions ───────────────────────────────────────────────────────────────
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
                PRIO_LABEL[p.priority], f"{p.memory}MB",
            ), tags=(tag,))

    def _run_all(self):
        for alg in ("FCFS", "SJF", "SRTF", "PRIO_NP", "PRIO_P", "RR", "MLQ", "MLFQ"):
            procs, timeline = run_algorithm(alg, self._procs)
            self._tabs[alg].update(procs, timeline)


if __name__ == "__main__":
    app = SchedulerGUI()
    app.mainloop()