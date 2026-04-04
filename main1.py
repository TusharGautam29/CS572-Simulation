import simpy
import random

class Process:
    def __init__(self, pid, arrival, burst):
        self.pid = pid
        self.arrival = arrival
        self.burst = burst

        self.start = 0
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
    # Sort by arrival
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

        print(f"{p.pid} starts at {p.start}")

        # Execute process
        yield env.timeout(p.burst)

        p.finish = env.now

        print(f"{p.pid} finishes at {p.finish}")

        # Metrics
        p.waiting = p.start - p.arrival
        p.turnaround = p.finish - p.arrival

        current_time = p.finish


def print_results(processes):
    total_waiting = 0
    total_turnaround = 0

    print("\nProcess Details:\n")

    for p in processes:
        print({
            "id": p.pid,
            "arrival": p.arrival,
            "burst": p.burst,
            "start": p.start,
            "finish": p.finish,
            "waiting": p.waiting,
            "turnaround": p.turnaround
        })

        total_waiting += p.waiting
        total_turnaround += p.turnaround

    n = len(processes)

    print("\nAverages:")
    print("Waiting Time =", total_waiting / n)
    print("Turnaround Time =", total_turnaround / n)


def run_simulation():
    SIM_TIME = 100

    processes = generate_processes(SIM_TIME)

    env = simpy.Environment()

    env.process(fcfs_simpy(env, processes))
    env.run()

    print_results(processes)


run_simulation()