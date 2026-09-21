"""
build_parallel.py — Real-time parallel multi-process PyInstaller build runner for SKC binaries.
Streams live build milestones and progress for both SKC_translation.exe and SKC_setup.exe.
"""
import os
import sys
import time
import shutil
import zipfile
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


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
        print(f"  [{tag:11s}] [*] Launching build process...")

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


def get_project_version(root_dir: Path) -> str:
    changelog_path = root_dir / "CHANGELOG.md"
    if changelog_path.exists():
        import re
        match = re.search(r"##\s*\[(\d+\.\d+\.\d+)\]", changelog_path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1)
    return "latest"


def create_distribution_zip(dist_dir: Path, root_dir: Path) -> Path | None:
    version = get_project_version(root_dir)
    zip_name = f"SKC_translate_v{version}.zip"
    zip_path = dist_dir / zip_name
    temp_zip = dist_dir / f"{zip_name}.tmp"

    exclude_prefixes = ("qr_",)
    exclude_suffixes = (".zip", ".log", ".tmp", ".bak")
    exclude_dirs = {"logs", "var", "__pycache__", ".pytest_cache"}
    exclude_exact = {".env", "token.txt"}

    print(f"\n  [*] Packaging distribution archive: {zip_name} ...")
    t0 = time.time()

    if temp_zip.exists():
        try:
            temp_zip.unlink()
        except Exception:
            pass

    entries_count = 0
    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(dist_dir):
            dirs[:] = [d for d in dirs if d.lower() not in exclude_dirs]
            rel_root = Path(root).relative_to(dist_dir)

            for file in sorted(files):
                file_lower = file.lower()
                # Security: Strictly exclude secret environment variables & tokens
                if file in exclude_exact or file_lower in exclude_exact:
                    continue
                if file_lower.startswith(".env") and file_lower != ".env.example":
                    continue
                if file_lower.endswith(exclude_suffixes):
                    continue
                if any(file_lower.startswith(p) for p in exclude_prefixes):
                    continue

                file_path = Path(root) / file
                arcname = (rel_root / file).as_posix()
                zf.write(file_path, arcname=arcname)
                entries_count += 1

    if zip_path.exists():
        try:
            zip_path.unlink()
        except Exception as e:
            print(f"  [WARN] Could not remove existing {zip_name}: {e}")

    shutil.move(str(temp_zip), str(zip_path))
    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    elapsed = time.time() - t0
    print(f"  [✅] Created {zip_name} ({zip_size_mb:.1f} MB, {entries_count} files) in {elapsed:.1f}s")
    return zip_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SKC Parallel Build Runner")
    parser.add_argument("-j", "--workers", type=int, default=0, help="Number of parallel worker processes (default: auto)")
    parser.add_argument("--no-zip", action="store_true", help="Skip generating distribution zip archive")
    parser.add_argument("--package-only", action="store_true", help="Only run post-build packaging and zip generation without rebuilding binaries")
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

    results = []
    wall_time = 0.0
    sum_task_time = 0.0

    if not args.package_only:
        t_start = time.time()
        # Run PyInstaller tasks concurrently across worker threads
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(run_pyinstaller, spec, work, out_dir)
                for spec, work in tasks
            ]
            for f in futures:
                results.append(f.result())

        wall_time = time.time() - t_start

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
        sum_task_time = sum(r[2] for r in results)
    else:
        print("  [*] Running in --package-only mode: skipping PyInstaller binary build.")

    # Post-build packaging
    branding_dir = dist_dir / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)

    def _safe_copy(src: Path, dst: Path) -> None:
        if not src.exists():
            return
        try:
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                return  # Identical, avoid file lock conflict
            shutil.copy2(src, dst)
            print(f"  [+] Copied {src.name} -> {dst.relative_to(root_dir)}")
        except PermissionError:
            print(f"  [WARN] {dst.name} is currently running/locked. Keeping existing file.")
        except Exception as e:
            print(f"  [WARN] Could not copy {src.name} -> {dst.name}: {e}")

    _safe_copy(root_dir / "branding" / "church-logo.png", branding_dir / "church-logo.png")
    _safe_copy(root_dir / "branding" / "church-logo.webp", branding_dir / "church-logo.webp")
    _safe_copy(root_dir / "config.yaml", dist_dir / "config.yaml")
    _safe_copy(root_dir / "CHANGELOG.md", dist_dir / "CHANGELOG.md")
    _safe_copy(root_dir / "how_to_use.html", dist_dir / "how_to_use.html")
    _safe_copy(root_dir / "cloudflared.exe", dist_dir / "cloudflared.exe")
    _safe_copy(root_dir / ".env.example", dist_dir / ".env.example")

    zip_file = None
    if not args.no_zip:
        zip_file = create_distribution_zip(dist_dir, root_dir)

    print("\n" + "=" * 70)
    print("  [SUCCESS] BUILD & PACKAGING COMPLETED!")
    if wall_time > 0:
        speedup = sum_task_time / wall_time if wall_time > 0 else 1.0
        time_saved = max(0.0, sum_task_time - wall_time)
        print(f"  Wall-Clock Time:  {wall_time:.1f}s (Actual real time elapsed)")
        print(f"  Sequential Time:  {sum_task_time:.1f}s (Sum of individual task compute times)")
        print(f"  Parallel Speedup: {speedup:.2f}x (Saved ~{time_saved:.1f}s on {max_workers} parallel workers)")
    print("=" * 70)
    print(f"  Output Directory: {dist_dir}")
    print("    ├── SKC_translation.exe")
    print("    ├── SKC_setup.exe")
    print("    ├── config.yaml")
    print("    ├── CHANGELOG.md")
    print("    ├── how_to_use.html")
    print("    ├── cloudflared.exe")
    print("    ├── branding\\")
    if zip_file and zip_file.exists():
        print(f"    └── {zip_file.name}  ({zip_file.stat().st_size / (1024 * 1024):.1f} MB distribution archive)")
    print("=" * 70)


if __name__ == "__main__":
    main()
