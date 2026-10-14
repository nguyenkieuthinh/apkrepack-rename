#!/usr/bin/env python3
"""Build the rename-test fixture APK without Gradle.

Pipeline: aapt2 compile -> aapt2 link (generates R.java) -> javac -> d8 ->
zip classes.dex in -> zipalign -> apksigner.

Two variants are produced:
  fixture.apk       no native library  -> lets the smali-touching modes run
  fixture-jni.apk   lib/arm64-v8a/libfixture.so exporting
                    Java_com_example_oldpkg_MainActivity_nativeHello
                    -> lets us test the JNI hard-block

Run:  python3 tests/build_fixture.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
BUILD = HERE / "build"

SDK = Path(os.environ.get("ANDROID_HOME", Path.home() / "Android" / "Sdk"))
BUILD_TOOLS = max(sorted((SDK / "build-tools").glob("*")), key=lambda p: [int(x) for x in p.name.split(".")])
PLATFORM = SDK / "platforms" / "android-35" / "android.jar"

AAPT2 = BUILD_TOOLS / "aapt2"
ZIPALIGN = BUILD_TOOLS / "zipalign"
APKSIGNER = BUILD_TOOLS / "apksigner"

KEYSTORE = Path.home() / ".android" / "debug.keystore"
KS_PASS = "android"
KS_ALIAS = "androiddebugkey"

PACKAGE = "com.example.oldpkg"


def run(cmd: list[str], **kw) -> None:
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable}")
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"FAILED ({proc.returncode}): {printable}")
    if proc.stdout.strip():
        for line in proc.stdout.strip().splitlines():
            print(f"    | {line}")


def need(path: Path, what: str) -> None:
    if not path.exists():
        raise SystemExit(f"missing {what}: {path}")


def compile_resources(res_zip: Path) -> None:
    print("[1/7] aapt2 compile")
    run([AAPT2, "compile", "--dir", FIXTURE / "res", "-o", res_zip])


def link_resources(res_zip: Path, base_apk: Path, gen_dir: Path) -> None:
    print("[2/7] aapt2 link (generates R.java)")
    shutil.rmtree(gen_dir, ignore_errors=True)
    gen_dir.mkdir(parents=True, exist_ok=True)
    run([
        AAPT2, "link",
        "-o", base_apk,
        "-I", PLATFORM,
        "--manifest", FIXTURE / "AndroidManifest.xml",
        "--java", gen_dir,
        res_zip,
    ])


def compile_java(gen_dir: Path, classes_dir: Path) -> None:
    print("[3/7] javac")
    shutil.rmtree(classes_dir, ignore_errors=True)
    classes_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(str(p) for p in (FIXTURE / "java").rglob("*.java"))
    sources += sorted(str(p) for p in gen_dir.rglob("*.java"))
    if not sources:
        raise SystemExit("no java sources found")
    # -source/-target rather than --release: we compile against android.jar, not the JDK API.
    run([
        "javac", "-source", "8", "-target", "8", "-nowarn",
        "-classpath", PLATFORM,
        "-d", classes_dir,
        *sources,
    ])


def build_dex(classes_dir: Path, dex_dir: Path) -> None:
    print("[4/7] d8")
    shutil.rmtree(dex_dir, ignore_errors=True)
    dex_dir.mkdir(parents=True, exist_ok=True)
    class_files = sorted(str(p) for p in classes_dir.rglob("*.class"))
    run(["d8", "--min-api", "24", "--output", dex_dir, *class_files])


def build_native(so_path: Path) -> None:
    """Cross-compile for aarch64 so the .so actually loads on a real arm64 device.

    The source includes no headers, so clang can target Android without an NDK
    sysroot. Falls back to the host compiler, which yields a .so that is still
    valid for *symbol detection* but cannot be dlopen'd on device.
    """
    print("[5/7] cross-compile JNI .so for arm64-v8a")
    src = FIXTURE / "jni" / "globalrefs.c"
    so_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["clang", "--target=aarch64-linux-android24", "-shared", "-fPIC",
             "-nostdlib", "-O0", "-o", so_path, src])
    except SystemExit:
        print("    | clang cross-compile unavailable, falling back to host gcc")
        run(["gcc", "-shared", "-fPIC", "-O0", "-o", so_path, src])
    nm = subprocess.run(["nm", "-D", "--defined-only", str(so_path)],
                        capture_output=True, text=True)
    if "Java_com_example_oldpkg_MainActivity_nativeHello" not in nm.stdout:
        raise SystemExit("JNI symbol missing from the built .so — detection test would be vacuous")
    print("    | symbol Java_com_example_oldpkg_MainActivity_nativeHello present")


def add_to_apk(base_apk: Path, dex_dir: Path, so_path: Path | None, out_apk: Path) -> None:
    print("[6/7] package classes.dex (+lib) into APK")
    shutil.copyfile(base_apk, out_apk)
    with zipfile.ZipFile(out_apk, "a", zipfile.ZIP_DEFLATED) as z:
        z.write(dex_dir / "classes.dex", "classes.dex")
        if so_path is not None:
            z.write(so_path, "lib/arm64-v8a/libfixture.so")


def align_and_sign(built_apk: Path, out_apk: Path, aligned: Path) -> None:
    print("[7/7] zipalign + apksigner")
    run([ZIPALIGN, "-f", "-p", "4", built_apk, aligned])
    run([
        APKSIGNER, "sign",
        "--ks", KEYSTORE, "--ks-pass", f"pass:{KS_PASS}",
        "--key-pass", f"pass:{KS_PASS}", "--ks-key-alias", KS_ALIAS,
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true",
        "--out", out_apk, aligned,
    ])
    run([APKSIGNER, "verify", "--verbose", out_apk])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["plain", "jni", "both"], default="both")
    args = ap.parse_args()

    for p, what in ((AAPT2, "aapt2"), (ZIPALIGN, "zipalign"), (APKSIGNER, "apksigner"),
                    (PLATFORM, "android.jar"), (KEYSTORE, "debug keystore")):
        need(p, what)

    variants = {
        "plain": ("fixture.apk", None),
        "jni": ("fixture-jni.apk", BUILD / "jni" / "arm64-v8a" / "libfixture.so"),
    }
    chosen = list(variants) if args.variant == "both" else [args.variant]

    res_zip = BUILD / "res.zip"
    base_apk = BUILD / "base.apk"
    gen_dir = BUILD / "gen"
    classes_dir = BUILD / "classes"
    dex_dir = BUILD / "dex"

    BUILD.mkdir(parents=True, exist_ok=True)
    compile_resources(res_zip)
    link_resources(res_zip, base_apk, gen_dir)
    compile_java(gen_dir, classes_dir)
    build_dex(classes_dir, dex_dir)

    main_so = variants["jni"][1]
    if "jni" in chosen:
        assert main_so is not None
        build_native(main_so)

    for name in chosen:
        filename, so = variants[name]
        raw = BUILD / f"{name}-unaligned.apk"
        aligned = BUILD / f"{name}-aligned.apk"
        out = HERE / "out" / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        add_to_apk(base_apk, dex_dir, so, raw)
        align_and_sign(raw, out, aligned)
        print(f"\n==> {out}  ({out.stat().st_size:,} bytes)")

    print(f"\npackage: {PACKAGE}")


if __name__ == "__main__":
    main()
