import tkinter as tk
from tkinter import ttk
import random

from config import *

# GUI
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

    def __init__(self, generate_fn, run_fn):
        super().__init__()
        self.title("OS Process Scheduler Simulator")
        self.geometry("1320x840")
        self.minsize(960, 660)
        self.configure(bg=BG)

        self._setup_styles()
        self._seed_var = tk.IntVar(value=42)
        self._procs    = []
        self.generate_processes = generate_fn
        self.run_algorithm = run_fn

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
        self._procs = self.generate_processes(SIM_TIME)
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
        results = {}
        for alg in ("FCFS", "SJF", "SRTF", "PRIO_NP", "PRIO_P", "RR", "MLQ", "MLFQ"):
            procs, timeline = self.run_algorithm(alg, self._procs)
            self._tabs[alg].update(procs, timeline)

            #compute metrics
            n = len(procs)
            avg_w  = sum(p.waiting for p in procs) / n
            avg_tt = sum(p.turnaround for p in procs) / n

            cpu = [e for e in timeline if e[0] == "cpu"]
            cache_logs = [e for e in timeline if e[0] == "cache"]

            hits = sum(1 for e in cache_logs if e[3] == "HIT")
            misses = sum(1 for e in cache_logs if e[3] == "MISS")

            hit_ratio = hits / (hits + misses) if (hits + misses) else 0

            max_t = max((e[3] for e in cpu), default=1)
            throughput = n / max_t if max_t else 0

            results[alg] = {
                "avg_w": avg_w,
                "avg_tt": avg_tt,
                "throughput": throughput,
                "cache_hit_ratio": hit_ratio
            }
        self._print_comparison(results)

    def _print_comparison(self, results):
        print("ALGORITHM COMPARISON")
        print("="*50)

        # print all results
        for alg, data in results.items():
            print(f"{alg:10} | WT: {data['avg_w']:.2f} | "
                f"TAT: {data['avg_tt']:.2f} | "
                f"TH: {data['throughput']:.3f} | "
                f"CacheHit: {data['cache_hit_ratio']:.2f}")

        # ranking
        best_wt  = min(results, key=lambda x: results[x]["avg_w"])
        best_tat = min(results, key=lambda x: results[x]["avg_tt"])
        best_th  = max(results, key=lambda x: results[x]["throughput"])

        print("\nBEST PER METRIC")
        print(f"Lowest Waiting Time     → {best_wt}")
        print(f"Lowest Turnaround Time  → {best_tat}")
        print(f"Highest Throughput      → {best_th}")
        
