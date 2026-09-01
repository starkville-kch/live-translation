"""
build_parallel.py — Real-time parallel multi-process PyInstaller build runner for SKC binaries.
Streams live build milestones and progress for both SKC_translation.exe and SKC_setup.exe.
"""
import os
import sys
import time
import shutil
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


print_lock = threading.Lock()


def run_pyinstaller(spec_name: str, work_subfolder: str, out_dir: Path) -> tuple[str, bool, float, str]:
    t0 = time.time()
    work_path = out_dir / "build" / work_subfolder
    dist_path = out_dir / "dist"
    tag = "Translation" if "translation" in spec_name.lower() else "Setup"
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        spec_name,
        "--noconfirm",
        "--workpath", str(work_path),
        "--distpath", str(dist_path),
    ]
    
    with print_lock:
        print(f"  [{tag:11s}] 🚀 Launching build process...")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )

    full_output = []
    milestone_keywords = [
        "analyzing", "collecting", "compiling", "building pkg", "building exe",
        "appending", "completed successfully", "warning:", "error:", "checking",
        "written to", "copying", "generating"
    ]

    last_milestone_time = time.time()

    for raw_line in proc.stdout:
        line = raw_line.strip()
        full_output.append(raw_line)
        if not line:
            continue
        
        # Filter and print key progress milestones to keep terminal clean yet responsive
        line_lower = line.lower()
        if any(k in line_lower for k in milestone_keywords):
            # Clean up INFO: prefixes
            display_line = line
            if "INFO:" in display_line:
                display_line = display_line.split("INFO:", 1)[1].strip()
            if len(display_line) > 75:
                display_line = display_line[:72] + "..."
            
            elapsed = time.time() - t0
            with print_lock:
                print(f"  [{tag:11s}] [{elapsed:4.1f}s] {display_line}")
            last_milestone_time = time.time()

    proc.wait()
    elapsed = time.time() - t0
    
    if proc.returncode == 0:
        with print_lock:
            print(f"  [{tag:11s}] ✅ Finished successfully in {elapsed:.1f}s")
        return spec_name, True, elapsed, ""
    else:
        with print_lock:
            print(f"  [{tag:11s}] ❌ Failed after {elapsed:.1f}s")
        err_msg = "".join(full_output)
        return spec_name, False, elapsed, err_msg


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SKC Parallel Build Runner")
    parser.add_argument("-j", "--workers", type=int, default=0, help="Number of parallel worker processes (default: auto)")
    args = parser.parse_args()

    root_dir = Path(__file__).parent.resolve()
    out_dir = root_dir / ".agent"
    dist_dir = out_dir / "dist"
    
    cpu_cores = os.cpu_count() or 4
    tasks = [
        ("SKC_translation.spec", "translation"),
        ("SKC_setup.spec", "setup"),
    ]
    
    max_workers = args.workers if args.workers > 0 else min(len(tasks), cpu_cores)

    print("=" * 70)
    print("  SKC Live Translation — Parallel Multi-Threaded Build Runner")
    print("=" * 70)
    print(f"  Project Root: {root_dir}")
    print(f"  Output Dir:   {dist_dir}")
    print(f"  Hardware:     {cpu_cores} CPU Cores detected (Allocated Workers: {max_workers})")
    print("=" * 70)
    print()

    os.chdir(root_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    results = []

    # Run PyInstaller tasks concurrently across worker threads
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(run_pyinstaller, spec, work, out_dir)
            for spec, work in tasks
        ]
        for f in futures:
            results.append(f.result())

    total_time = time.time() - t_start

    # Check results
    failed = [r for r in results if not r[1]]
    if failed:
        print("\n" + "=" * 70)
        print("  [ERROR] One or more builds failed:")
        for name, ok, el, err in failed:
            print(f"\n--- Error in {name} ---")
            print(err[-1500:])
        print("=" * 70)
        sys.exit(1)

    # Post-build packaging
    branding_dir = dist_dir / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)
    
    src_logo = root_dir / "branding" / "church-logo.png"
    if src_logo.exists():
        shutil.copy2(src_logo, branding_dir / "church-logo.png")

    src_config = root_dir / "config.yaml"
    if src_config.exists():
        shutil.copy2(src_config, dist_dir / "config.yaml")

    sum_task_time = sum(r[2] for r in results)
    wall_time = total_time
    speedup = sum_task_time / wall_time if wall_time > 0 else 1.0
    time_saved = max(0.0, sum_task_time - wall_time)

    print("\n" + "=" * 70)
    print("  🎉 PARALLEL BUILD SUCCEEDED!")
    print(f"  Wall-Clock Time:  {wall_time:.1f}s (Actual real time elapsed)")
    print(f"  Sequential Time:  {sum_task_time:.1f}s (Sum of individual task compute times)")
    print(f"  Parallel Speedup: {speedup:.2f}x (Saved ~{time_saved:.1f}s on {max_workers} parallel workers)")
    print("=" * 70)
    print(f"  Output Directory: {dist_dir}")
    print("    ├── SKC_translation.exe")
    print("    ├── SKC_setup.exe")
    print("    ├── config.yaml")
    print("    └── branding\\")
    print("          └── church-logo.png")
    print("=" * 70)


if __name__ == "__main__":
    main()
