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

        while not done:
            if ready_queue is empty:
                yield wakeup_ref[0]      # ← sleep here
                wakeup_ref[0] = env.event()   # re-arm for next sleep
                continue
            ... run next process ...
            notify()   # ← wake self in case new jobs appeared during run

    WHY NOT JUST poll with timeout(1)?

    Polling wastes sim ticks and produces incorrect timing.  The notifier
    pattern guarantees the scheduler wakes up *at the exact SimPy tick*
    when a process enters the ready queue, keeping statistics accurate.
    """

"""
Compute the context-switch overhead for switching the CPU to process `p`.

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
