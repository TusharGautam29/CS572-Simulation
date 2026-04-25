from config import *

# notifier 
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

def _do_ctx_switch(p, prev_pid, cache):
    
    if prev_pid is not None and prev_pid == p.pid:
        cache.hits+=1
        return 0, True                          # same process — no cost

    is_hit     = cache.access(p.pid, p.cache_required)
    p.in_cache = True                           # process is now in cache
    penalty    = CONTEXT_SWITCH_COST + (0 if is_hit else CACHE_MISS_PENALTY)
    return penalty, is_hit

#Scheduling algorithms  —  each now accepts `cache` as a parameter
#  Integration points:
#    ① _do_ctx_switch()  called before every CPU burst
#    ② cache.evict(pid)  called after every RAM free (process done)

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

        # context switch
        penalty, is_hit = _do_ctx_switch(p, prev_pid, cache)
        if penalty:
            cs_t0 = env.now
            yield env.timeout(penalty)
            timeline.append(("ctx_switch", p.pid, cs_t0, env.now, is_hit))

        if p.start == -1:
            p.start = env.now

        # execution
        TIME_SLICE = 2
        run = min(remaining[p.pid], TIME_SLICE)

        t0 = env.now
        yield env.timeout(run)
        remaining[p.pid] -= run

        timeline.append(("cpu", p.pid, t0, env.now, p.priority))

        # update state
        prev_pid = p.pid

        if remaining[p.pid] == 0:
            p.finish     = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting    = p.turnaround - p.burst
            done        += 1
            current      = None
            cache.evict(p.pid)
            p.in_cache = False
            yield ram.free(p.memory)
            yield env.timeout(0)
        else:
            current = p
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
