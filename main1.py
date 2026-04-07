import simpy
import random
from copy import deepcopy


class Process:
    def __init__(self, pid, arrival, burst):
        self.pid = pid
        self.arrival = arrival
        self.burst = burst

        self.start = -1
        self.finish = 0
        self.waiting = 0
        self.turnaround = 0


def generate_processes(sim_time):
    processes = []
    current_time = 0
    pid = 1

    while current_time < sim_time:
        gap = random.randint(1, 5)
        current_time += gap

        if current_time > sim_time:
            break

        burst = random.randint(2, 8)
        p = Process(f"P{pid}", current_time, burst)
        processes.append(p)
        pid += 1

    return processes



def fcfs_simpy(env, processes):
    processes.sort(key=lambda p: p.arrival)
    current_time = 0

    for p in processes:
        # Wait until process arrives
        if env.now < p.arrival:
            yield env.timeout(p.arrival - env.now)

        # CPU idle handling
        if current_time < p.arrival:
            current_time = p.arrival

        p.start = current_time
        print(f"  {p.pid} starts at {p.start}")

        yield env.timeout(p.burst)

        p.finish = env.now
        p.waiting = p.start - p.arrival
        p.turnaround = p.finish - p.arrival
        current_time = p.finish

        print(f"  {p.pid} finishes at {p.finish}")


# ─── Round Robin ─────────────────────────────────────────────────────────────

def enqueue_arrivals(processes, ready_queue, arrived_idx, current_time):
    while arrived_idx < len(processes) and processes[arrived_idx].arrival <= current_time:
        ready_queue.append(processes[arrived_idx])
        arrived_idx += 1
    return arrived_idx


def round_robin_simpy(env, processes, quantum):
    processes.sort(key=lambda p: p.arrival)

    remaining = {p.pid: p.burst for p in processes}
    ready_queue = []
    arrived_idx = 0

    if processes and env.now < processes[0].arrival:
        yield env.timeout(processes[0].arrival - env.now)

    arrived_idx = enqueue_arrivals(processes, ready_queue, arrived_idx, env.now)

    while ready_queue or arrived_idx < len(processes):
        if not ready_queue:
            yield env.timeout(processes[arrived_idx].arrival - env.now)
            arrived_idx = enqueue_arrivals(processes, ready_queue, arrived_idx, env.now)

        p = ready_queue.pop(0)

        if p.start == -1:
            p.start = env.now
            print(f"  {p.pid} first runs at {p.start}")

        run_for = min(quantum, remaining[p.pid])
        yield env.timeout(run_for)
        remaining[p.pid] -= run_for

        arrived_idx = enqueue_arrivals(processes, ready_queue, arrived_idx, env.now)

        if remaining[p.pid] == 0:
            p.finish = env.now
            p.turnaround = p.finish - p.arrival
            p.waiting = p.turnaround - p.burst
            print(f"  {p.pid} finishes at {p.finish}")
        else:
            ready_queue.append(p)


# ─── Results ─────────────────────────────────────────────────────────────────

def print_results(processes, algorithm_name):
    total_waiting = 0
    total_turnaround = 0

    print(f"\n{'═' * 55}")
    print(f"  {algorithm_name} — Process Details")
    print(f"{'═' * 55}")
    print(f"{'PID':<6} {'Arrival':>7} {'Burst':>5} {'Start':>6} {'Finish':>7} {'Wait':>5} {'TAT':>5}")
    print(f"{'─' * 55}")

    for p in processes:
        print(f"{p.pid:<6} {p.arrival:>7} {p.burst:>5} {p.start:>6} {p.finish:>7} {p.waiting:>5} {p.turnaround:>5}")
        total_waiting += p.waiting
        total_turnaround += p.turnaround

    n = len(processes)
    print(f"{'─' * 55}")
    print(f"  Avg Waiting Time    : {total_waiting / n:.2f}")
    print(f"  Avg Turnaround Time : {total_turnaround / n:.2f}")
    print(f"{'═' * 55}\n")


# ─── Runner ──────────────────────────────────────────────────────────────────

def run_simulation():
    SIM_TIME = 100
    QUANTUM = 3

    original_processes = generate_processes(SIM_TIME)
    fcfs_processes = deepcopy(original_processes)
    rr_processes = deepcopy(original_processes)

    print("\n>>> Running FCFS...\n")
    env_fcfs = simpy.Environment()
    env_fcfs.process(fcfs_simpy(env_fcfs, fcfs_processes))
    env_fcfs.run()

    print(f"\n>>> Running Round Robin (quantum={QUANTUM})...\n")
    env_rr = simpy.Environment()
    env_rr.process(round_robin_simpy(env_rr, rr_processes, quantum=QUANTUM))
    env_rr.run()

    print_results(fcfs_processes, "FCFS")
    print_results(rr_processes, f"Round Robin (quantum={QUANTUM})")


run_simulation()