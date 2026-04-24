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
