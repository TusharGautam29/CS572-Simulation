from config import *

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

def get_cache_factor(pid, now, last_run):
    if not CACHE_ENABLED:
        return 1.0

    if pid in last_run:
        if now - last_run[pid] <= CACHE_WINDOW:
            return CACHE_HIT_FACTOR
        else:
            return CACHE_MISS_FACTOR

    return CACHE_MISS_FACTOR

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
    last_run = {}
    cache_hits = 0
    cache_misses = 0      
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
        factor = get_cache_factor(p.pid, env.now, last_run)
        if p.pid in last_run and env.now - last_run[p.pid] <= CACHE_WINDOW:
            cache_hits += 1
            cache_state = "HIT"
        else:
            cache_misses += 1
            cache_state = "MISS"
        yield env.timeout(max(1, int(1 * factor))) 

        remaining[p.pid] -= 1
        last_run[p.pid] = env.now

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        timeline.append(("cache", p.pid, env.now, cache_state, factor))

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
    last_run = {}
    cache_hits = 0
    cache_misses = 0
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

        factor = get_cache_factor(p.pid, env.now, last_run)
        if p.pid in last_run and env.now - last_run[p.pid] <= CACHE_WINDOW:
            cache_hits += 1
            cache_state = "HIT"
        else:
            cache_misses += 1
            cache_state = "MISS"
        yield env.timeout(max(1, int(1 * factor)))
        remaining[p.pid] -= 1
        last_run[p.pid] = env.now

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        timeline.append(("cache", p.pid, env.now, cache_state, factor))

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
    last_run = {}
    cache_hits = 0
    cache_misses = 0
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
        factor = get_cache_factor(p.pid, env.now, last_run)
        if p.pid in last_run and env.now - last_run[p.pid] <= CACHE_WINDOW:
            cache_hits += 1
            cache_state = "HIT"
        else:
            cache_misses += 1
            cache_state = "MISS"
        adjusted_run = max(1, int(run * factor))

        yield env.timeout(adjusted_run)

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        timeline.append(("cache", p.pid, env.now, cache_state, factor))

        remaining[p.pid] -= run
        last_run[p.pid] = env.now

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
    last_run = {}
    cache_hits = 0
    cache_misses = 0
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
        
        factor = get_cache_factor(p.pid, env.now, last_run)
        if p.pid in last_run and env.now - last_run[p.pid] <= CACHE_WINDOW:
            cache_hits += 1
            cache_state = "HIT"
        else:
            cache_misses += 1
            cache_state = "MISS"
        adjusted_run = max(1, int(run * factor))
        yield env.timeout(adjusted_run)

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))
        timeline.append(("cache", p.pid, env.now, cache_state, factor))
        
        remaining[p.pid] -= run
        last_run[p.pid] = env.now

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
    last_run = {}
    cache_hits = 0
    cache_misses = 0
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

        factor = get_cache_factor(p.pid, env.now, last_run)
        if p.pid in last_run and env.now - last_run[p.pid] <= CACHE_WINDOW:
            cache_hits += 1
            cache_state = "HIT"
        else:
            cache_misses += 1
            cache_state = "MISS"
        adjusted_run = max(1, int(run * factor))
        yield env.timeout(adjusted_run)

        # store queue LEVEL (not original priority) for colour-coding
        timeline.append(("cpu", p.pid, t0, env.now, prio))

        last_cpu[p.pid]  = env.now
        remaining[p.pid] -= run
        last_run[p.pid] = env.now

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
