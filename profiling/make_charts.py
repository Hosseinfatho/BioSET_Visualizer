"""
Parse the BioSET profiling logs and render simple comparison charts.

Only uses values that are directly measured in the logs (compute time, render
time, end-to-end latency, cache hit/miss counts) — nothing inferred.

Stages (by code state, detected from log contents):
  Baseline                 : original pipeline (render on main thread in apply)
  Phase 1+2                : latest-wins loader + off-thread vtk build + progressive
  Per-channel + batched    : parallel per-channel display + render moved out of apply
"""
import os
import re
import statistics as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "charts")
os.makedirs(OUT, exist_ok=True)

STAGES = [
    ("Baseline",              "bioset_profile_20260630_174118.log"),
    ("Phase 1+2",             "bioset_profile_20260630_180856.log"),
    ("Per-channel\n+ batched", "bioset_profile_20260630_183813.log"),
]

re_compute = re.compile(r"compute=([\d.]+)ms")
re_src     = re.compile(r"src=([A-Z\-]+)")
re_chunks  = re.compile(r"chunks\(disk=(\d+),remote=(\d+)\)")
re_vtk     = re.compile(r"\[vtk-image\].*?total=([\d.]+)ms")
re_apply_t = re.compile(r"\[apply-main\].*?total=([\d.]+)ms")
re_render  = re.compile(r"render=([\d.]+)ms")
re_e2e     = re.compile(r"end_to_end_latency=([\d.]+)ms")
re_mem     = re.compile(r"mem-cache\[hits=(\d+) misses=(\d+) rate=(\d+)%")
re_disk    = re.compile(r"disk-cache\[hits=(\d+) misses\(remote\)=(\d+) rate=(\d+)%")


def parse(path):
    d = dict(compute_disk=[], compute_remote=[], vtk=[], render=[], e2e=[],
             mem_last=(0, 0, 0), disk_last=(0, 0, 0))
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "[load]" in line:
                m = re_compute.search(line)
                src = re_src.search(line)
                ch = re_chunks.search(line)
                if m and src:
                    val = float(m.group(1))
                    remote = int(ch.group(2)) if ch else 0
                    if remote > 0:
                        d["compute_remote"].append(val)
                    elif src.group(1) == "DISK":
                        d["compute_disk"].append(val)
                mm = re_mem.search(line)
                if mm:
                    d["mem_last"] = (int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
                dm = re_disk.search(line)
                if dm:
                    d["disk_last"] = (int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
            if "[vtk-image]" in line:
                m = re_vtk.search(line)
                if m:
                    d["vtk"].append(float(m.group(1)))
            if "[apply-main]" in line:
                r = re_render.search(line)
                if r:
                    d["render"].append(float(r.group(1)))
                e = re_e2e.search(line)
                if e:
                    d["e2e"].append(float(e.group(1)))
    return d


data = {name: parse(os.path.join(HERE, fn)) for name, fn in STAGES}
names = [s[0] for s in STAGES]
COL = {"Baseline": "#c0504d", "Phase 1+2": "#4f81bd", "Per-channel\n+ batched": "#9bbb59"}
colors = [COL[n] for n in names]


def med(xs):
    return st.median(xs) if xs else 0.0


# ── Figure 1: where time goes in the BASELINE pipeline ──────────────
b = data["Baseline"]
comps = [
    ("compute() — disk/cached", med(b["compute_disk"])),
    ("compute() — worst (remote)", max(b["compute_remote"]) if b["compute_remote"] else 0),
    ("numpy→vtk build (main thread)", med(b["vtk"])),
    ("GPU render (main thread)", med(b["render"])),
]
labels = [c[0] for c in comps]
vals = [c[1] for c in comps]
fig, ax = plt.subplots(figsize=(9, 4.2))
bars = ax.barh(labels, vals, color=["#4f81bd", "#c0504d", "#f0a500", "#8064a2"])
ax.set_xscale("log")
ax.set_xlabel("milliseconds (log scale)")
ax.set_title("1. Where time goes — BASELINE pipeline (per load/swap)")
for bar, v in zip(bars, vals):
    ax.text(v * 1.1, bar.get_y() + bar.get_height() / 2,
            f"{v:,.0f} ms", va="center", fontsize=9)
ax.invert_yaxis()
plt.tight_layout()
plt.savefig(os.path.join(OUT, "fig1_baseline_breakdown.png"), dpi=120)
plt.close()


# ── Figure 2: main-thread blocking per swap, across stages ──────────
# Baseline main-thread work = numpy->vtk build + render (both on main thread).
# Phase 1+2 = render only (build moved off-thread).
# Per-channel+batched = render moved out of apply -> not separately timed.
main_block = [
    med(b["vtk"]) + med(b["render"]),
    med(data["Phase 1+2"]["render"]),
    med(data["Per-channel\n+ batched"]["render"]),  # empty -> 0
]
fig, ax = plt.subplots(figsize=(8, 4.5))
bars = ax.bar(names, main_block, color=colors)
ax.set_ylabel("ms of main-thread blocking per swap (median)")
ax.set_title("2. Main-thread freeze per swap (lower = more interactive)")
for bar, v, n in zip(bars, main_block, names):
    if v > 0:
        ax.text(bar.get_x() + bar.get_width() / 2, v + 5, f"{v:.0f} ms",
                ha="center", fontsize=10)
    else:
        ax.text(bar.get_x() + bar.get_width() / 2, 8,
                "render moved to\nbatched drain\n(not timed)",
                ha="center", fontsize=8, color="gray")
plt.tight_layout()
plt.savefig(os.path.join(OUT, "fig2_mainthread_blocking.png"), dpi=120)
plt.close()


# ── Figure 3: the remote wall (unsolved) vs cached compute ──────────
disk_med = [med(data[n]["compute_disk"]) for n in names]
remote_max = [max(data[n]["compute_remote"]) / 1000.0 if data[n]["compute_remote"] else 0
              for n in names]
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.3))
ax1.bar(names, disk_med, color=colors)
ax1.set_ylabel("ms (median)")
ax1.set_title("3a. Cached compute() — fast & stable")
for i, v in enumerate(disk_med):
    ax1.text(i, v + 10, f"{v:.0f} ms", ha="center", fontsize=9)
ax2.bar(names, remote_max, color=colors)
ax2.set_ylabel("seconds (max)")
ax2.set_title("3b. WORST remote compute() — unchanged (Phase 3)")
for i, v in enumerate(remote_max):
    ax2.text(i, v + 1, f"{v:.0f} s", ha="center", fontsize=10)
plt.tight_layout()
plt.savefig(os.path.join(OUT, "fig3_remote_wall.png"), dpi=120)
plt.close()


# ── Figure 4: cache hits / misses per stage ─────────────────────────
mem_hits = [data[n]["mem_last"][0] for n in names]
mem_miss = [data[n]["mem_last"][1] for n in names]
disk_hits = [data[n]["disk_last"][0] for n in names]
disk_rem = [data[n]["disk_last"][1] for n in names]   # remote misses (chunks)
disk_rate = [data[n]["disk_last"][2] for n in names]

fig, (axA, axB) = plt.subplots(1, 2, figsize=(11, 4.3))
x = range(len(names))
w = 0.38
axA.bar([i - w / 2 for i in x], mem_hits, w, label="hits", color="#4f81bd")
axA.bar([i + w / 2 for i in x], mem_miss, w, label="misses", color="#c0504d")
axA.set_xticks(list(x)); axA.set_xticklabels(names)
axA.set_ylabel("count (end of session)")
axA.set_title("4a. In-memory array cache (hits vs misses)")
axA.legend()

axB.bar([i - w / 2 for i in x], disk_hits, w, label="disk hits", color="#9bbb59")
axB.bar([i + w / 2 for i in x], disk_rem, w, label="remote misses (chunks)", color="#c0504d")
axB.set_xticks(list(x)); axB.set_xticklabels(names)
axB.set_ylabel("chunk count (cumulative)")
axB.set_title("4b. On-disk chunk cache (disk hits vs remote fetches)")
for i, r in enumerate(disk_rate):
    axB.text(i, disk_hits[i] + 150, f"{r}% hit", ha="center", fontsize=9, color="#5a7d1e")
axB.legend()
plt.tight_layout()
plt.savefig(os.path.join(OUT, "fig4_cache.png"), dpi=120)
plt.close()

# ── print the numbers backing each chart ──
print("STAGE                  disk-compute(med)  remote-compute(max)  render(med)  mem(h/m)  disk hit%")
for n in names:
    d = data[n]
    print(f"{n.replace(chr(10),' '):22s} "
          f"{med(d['compute_disk']):7.0f} ms        "
          f"{(max(d['compute_remote'])/1000 if d['compute_remote'] else 0):6.1f} s          "
          f"{med(d['render']):6.0f} ms   "
          f"{d['mem_last'][0]}/{d['mem_last'][1]:<4}  "
          f"{d['disk_last'][2]}%")
print("\nCharts written to", OUT)
