# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Background-Report is a cross-platform Python tool that checks computer hardware health (CPU, memory, storage, GPU, battery) and assigns an overall score from 1 (very poor) to 10 (perfect). It includes:

- `hardware_health_checker.py` — backend library + CLI entry point (`HardwareChecker`, `StressTester`, `main()`)
- `gpu_test_ui.py` — Gradio web UI (health check, stress test, performance benchmark tabs) that imports `HardwareChecker`/`StressTester` from the backend
- `GPU_TEST_UI_README.md` — documentation for the web UI
- Reports are written to `hardware_health_report.json` and `stress_test_report.json`

## Commands

**Run the CLI (interactive menu):**
```
python hardware_health_checker.py
```

**Run a specific mode directly:**
```
python hardware_health_checker.py --mode health                    # point-in-time health snapshot only
python hardware_health_checker.py --mode stress --duration standard --yes   # sustained-load stress test (skip confirmation)
python hardware_health_checker.py --mode both                      # health + stress test
```

**Duration tiers:** `quick` (~60s/component), `standard` (~300s/component), `extended` (~900s/component)

**Launch the Gradio web UI:**
```
python gpu_test_ui.py
# defaults to http://localhost:7860; add --server-name 0.0.0.0 --server-port <port> for network access
```

**Install Python dependencies:**
```
pip install psutil gradio
# Optional, for GPU stress/benchmark compute load:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

**Optional tooling (improves detection accuracy):**
- `nvidia-smi` on PATH — NVIDIA GPU temperature, utilization, VRAM, driver info (any OS)
- Windows: PowerShell `Get-CimInstance` / `Get-PhysicalDisk` (built-in, no extra install)
- Linux: `dmidecode`, `smartctl`, `lsblk` (sudo for some details)
- macOS: `system_profiler`, `diskutil` (built-in)

**Exit code:** `main()` returns `0` if overall score >= 5, `1` otherwise.

## Architecture

**Two classes, one module.** Everything lives in `hardware_health_checker.py`:

- `HardwareChecker` — runs point-in-time health checks. `run_full_check()` collects `system`, `cpu`, `memory`, `storage`, `gpu`, `battery` into `self.results['components']`, scores each into `self.results['scores']`, computes `self.results['overall_score']`, and `generate_report()` renders the text report. Each check method (`check_cpu`, `check_memory`, `check_storage`, `check_gpu`, `check_battery`) returns a dict with `health_status` (`good`/`warning`/`critical`/`unknown`), `issues` (list of strings), and metrics.

- `StressTester` — runs sustained-load tests to surface problems that only appear over time. `stress_cpu`, `stress_memory`, `stress_disk`, `stress_gpu` each return a result dict with `health_status`, `issues`, and test-specific metrics. `run_full_stress(duration_seconds, duration_tier)` runs all four back-to-back.

**Scoring:** each component starts at 10; each `Critical` issue subtracts 3, `Warning` subtracts 2, `Notice` subtracts 1. `health_status == 'critical'` caps at 3, `warning` caps at 6. Overall score is a weighted average: CPU 0.25, memory 0.20, storage 0.25, GPU 0.15, battery 0.15. A missing battery scores 0 but is excluded from the weighted average.

**Cross-platform branching:** every check method branches on `IS_WINDOWS` / `IS_LINUX` / `IS_MAC` (module-level booleans from `platform.system()`). Windows uses PowerShell (`run_powershell`) for CIM/WMI queries; Linux/macOS use shell commands (`run_shell`) and `psutil`. `nvidia-smi` is queried the same way on all OSes when present on PATH.

**The Gradio UI** (`gpu_test_ui.py`) wraps the backend: `GPUTester` instantiates `HardwareChecker` and `StressTester`, then `create_ui()` builds three tabs (Health Check, Stress Test, Performance Benchmark). `ui.launch(server_name="0.0.0.0", server_port=7860, share=False)` at the bottom — edit this line to change host/port or enable public sharing.

## Key files to touch for common changes

- Add a new hardware component check → add a method to `HardwareChecker`, call it from `run_full_check()`, add scoring entry in `calculate_overall_score()` weights, render it in `generate_report()`.
- Change scoring thresholds → `calculate_component_score()` and `calculate_overall_score()` in `hardware_health_checker.py`.
- Add a UI tab → `create_ui()` in `gpu_test_ui.py`; wire a button click to a `GPUTester` method.
- Change the launch URL/port → bottom of `gpu_test_ui.py`, the `ui.launch(...)` call.

## Output artifacts

- `hardware_health_report.json` — JSON dump of `HardwareChecker.results` after a health run.
- `stress_test_report.json` — JSON dump of `run_full_stress()` results after a stress run.
- `__pycache__/` — compiled Python cache (ignore).