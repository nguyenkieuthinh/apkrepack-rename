#!/usr/bin/env python3
"""MEASUREMENT HARNESS (throwaway experiment, not the shipped tool).

Runs one rename strategy end to end against the fixture APK and reports what
broke, using the connected device's logcat as the oracle.

Modes
  a  manifest rewrite only; smali untouched
  b  a + rewrite Lcom/example/oldpkg/R$* references in smali (no file moves)
  c  a + move the whole smali namespace tree AND rewrite all class references
  d  apktool 2.x build --rename-manifest-package (apktool does it itself)

Usage: python3 tests/measure_modes.py --mode a [--apk PATH] [--new PKG]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APKTOOL3 = ROOT / ".tools" / "apktool_3.0.3.jar"
APKTOOL2 = ROOT / ".tools" / "apktool_2.12.1.jar"

SDK = Path(os.environ.get("ANDROID_HOME", Path.home() / "Android" / "Sdk"))
BUILD_TOOLS = max(sorted((SDK / "build-tools").glob("*")), key=lambda p: [int(x) for x in p.name.split(".")])
ZIPALIGN = BUILD_TOOLS / "zipalign"
APKSIGNER = BUILD_TOOLS / "apksigner"
ADB = SDK / "platform-tools" / "adb"

KEYSTORE = Path.home() / ".android" / "debug.keystore"

ANDROID_NS = "http://schemas.android.com/apk/res/android"

# Attributes whose values name a *class*. Left alone in modes a/b (classes stay
# where they are); rewritten in mode c (classes move with the namespace).
CLASS_ATTRS = {
    "name", "targetActivity", "parentActivityName",
}
CLASS_TAGS = {"application", "activity", "activity-alias", "service", "receiver",
              "provider", "instrumentation"}


def A(attr: str) -> str:
    return f"{{{ANDROID_NS}}}{attr}"


def run(cmd, check=True, capture=True):
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and proc.returncode != 0:
        print(f"  !! {' '.join(str(c) for c in cmd)}")
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
        raise SystemExit(f"command failed: {cmd[0]}")
    return proc


def apktool(jar: Path, args: list, apk_or_dir: Path, **kw):
    return run(["java", "-jar", jar, *args, apk_or_dir], **kw)


def split(tag: str):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


# --------------------------------------------------------------------------- manifest

def register_namespaces(text: str) -> None:
    head = text[: text.find(">") + 1]
    for m in re.finditer(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', head):
        ET.register_namespace(m.group(1), m.group(2))


def expand_relative_names(root, old: str) -> list[str]:
    """'.Bar' -> 'com.example.oldpkg.Bar' BEFORE the package attribute moves.

    Otherwise aapt2 would resolve '.Bar' against the NEW package while the class
    still lives in the old one.
    """
    changed = []
    for el in root.iter():
        if split(el.tag) not in CLASS_TAGS:
            continue
        for attr in ("name", "targetActivity", "parentActivityName"):
            key = A(attr)
            val = el.get(key)
            if val and val.startswith("."):
                el.set(key, old + val)
                changed.append(f"{split(el.tag)}/{attr}: '{val}' -> '{old + val}'")
    return changed


def rewrite_manifest(man: Path, old: str, new: str, mode: str) -> dict:
    text = man.read_text("utf-8")
    register_namespaces(text)
    root = ET.fromstring(text)

    report = {"relative_expanded": [], "package": None, "identifier_values": [],
              "class_values": [], "untouched_samples": []}

    report["relative_expanded"] = expand_relative_names(root, old)

    # 1. the package attribute itself
    if root.get("package") == old:
        root.set("package", new)
        report["package"] = f"{old} -> {new}"

    rewrite_classes = mode == "c"

    for el in root.iter():
        tag = split(el.tag)
        for key, val in list(el.attrib.items()):
            if val is None or old not in val:
                continue
            if key == "package":
                continue  # handled above
            is_class_attr = (tag in CLASS_TAGS and key == A("name")) or key in (
                A("targetActivity"), A("parentActivityName"))
            # <meta-data> values and resource refs must survive untouched
            if key == A("resource") or key == A("value"):
                report["untouched_samples"].append(f"{tag}/{split(key)} = {val}")
                continue
            if is_class_attr:
                if rewrite_classes:
                    el.set(key, val.replace(old, new))
                    report["class_values"].append(f"{tag}/{split(key)}: {val} -> {val.replace(old, new)}")
                continue
            newval = val.replace(old, new)
            el.set(key, newval)
            report["identifier_values"].append(f"{tag}/{split(key)}: {val} -> {newval}")

    body = ET.tostring(root, encoding="unicode")
    man.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + body.split("?>", 1)[-1].lstrip() , "utf-8")
    return report


# --------------------------------------------------------------------------- smali

def smali_dirs(decoded: Path) -> list[Path]:
    return sorted(p for p in decoded.iterdir()
                  if p.is_dir() and (p.name == "smali" or p.name.startswith("smali_")))


def smali_stats(decoded: Path, old: str) -> dict:
    old_path = old.replace(".", "/")
    refs = 0
    r_refs = 0
    for d in smali_dirs(decoded):
        for f in d.rglob("*.smali"):
            t = f.read_text("utf-8", errors="ignore")
            refs += t.count(f"L{old_path}/")
            r_refs += t.count(f"L{old_path}/R$")
    ns_dirs = [d for d in smali_dirs(decoded) if (d / old_path).is_dir()]
    return {"class_refs": refs, "r_refs": r_refs, "namespace_dirs": ns_dirs}


def rewrite_smali(decoded: Path, old: str, new: str, mode: str) -> dict:
    old_path, new_path = old.replace(".", "/"), new.replace(".", "/")
    stats = smali_stats(decoded, old)
    report = dict(stats)
    report["files_touched"] = 0
    report["refs_replaced"] = 0

    if mode == "a":
        return report

    if mode == "b":
        pat = re.compile(rf"L{re.escape(old_path)}/R\$")
        repl = f"L{new_path}/R$"
    else:
        pat = re.compile(rf"L{re.escape(old_path)}/")
        repl = f"L{new_path}/"

    for d in smali_dirs(decoded):
        for f in d.rglob("*.smali"):
            t = f.read_text("utf-8", errors="ignore")
            t2, n = pat.subn(repl, t)
            if n:
                f.write_text(t2, "utf-8")
                report["files_touched"] += 1
                report["refs_replaced"] += n

    if mode == "c":
        moved = []
        for d in smali_dirs(decoded):
            src = d / old_path
            if src.is_dir():
                dst = d / new_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(src), str(dst))
                moved.append(str(src.relative_to(decoded)))
                # clean up now-empty parent dirs
                cur = src.parent
                while cur != d:
                    try:
                        cur.rmdir()
                    except OSError:
                        break
                    cur = cur.parent
        report["dirs_moved"] = moved
    return report


# --------------------------------------------------------------------------- pipeline

def set_rename_manifest_package(yml: Path, new: str) -> None:
    lines = yml.read_text("utf-8").splitlines()
    out, inserted, in_pkg = [], False, False
    for line in lines:
        if line.startswith("packageInfo:"):
            in_pkg = True
            out.append(line)
            continue
        if in_pkg and not line.startswith((" ", "-")) and line.strip():
            if not inserted:
                out.append(f"  renameManifestPackage: {new}")
                inserted = True
            in_pkg = False
        out.append(line)
    if in_pkg and not inserted:
        out.append(f"  renameManifestPackage: {new}")
        inserted = True
    if not inserted:
        out.append("packageInfo:")
        out.append(f"  renameManifestPackage: {new}")
    yml.write_text("\n".join(out) + "\n", "utf-8")


def build_sign(decoded: Path, jar: Path, unsigned: Path, aligned: Path,
               signed: Path, extra_build_args: list) -> None:
    unsigned.parent.mkdir(parents=True, exist_ok=True)
    apktool(jar, ["b", *extra_build_args, "-o", str(unsigned)], decoded)
    run([ZIPALIGN, "-f", "-p", "4", unsigned, aligned])
    run([APKSIGNER, "sign", "--ks", KEYSTORE, "--ks-pass", "pass:android",
         "--key-pass", "pass:android", "--ks-key-alias", "androiddebugkey",
         "--out", signed, aligned])
    v = run([APKSIGNER, "verify", "--verbose", signed])
    return v.stdout


def measure(pkg: str, timeout: int = 6) -> dict:
    run([ADB, "shell", "am", "force-stop", pkg], check=False)
    run([ADB, "logcat", "-c"], check=False)
    start = run([ADB, "shell", "monkey", "-p", pkg, "-c",
                 "android.intent.category.LAUNCHER", "1"], check=False)
    import time
    time.sleep(timeout)
    logs = run([ADB, "logcat", "-d", "-s", "RenameFixture:*"], check=False).stdout
    crash = run([ADB, "logcat", "-d", "-b", "crash"], check=False).stdout
    pid = run([ADB, "shell", "pidof", pkg], check=False).stdout.strip()
    return {"logs": logs, "crash": crash, "pid": pid, "start": start.stdout + start.stderr}


def evaluate(m: dict) -> tuple[str, list[str]]:
    logs = m["logs"]
    notes = []
    if "FIXTURE-OK" not in logs:
        return "CRASH/NO-RUN", ["FIXTURE-OK never logged"]
    if "caseA-OK" not in logs:
        notes.append("caseA (R class) FAILED")
    if "caseB-FAILED" in logs:
        notes.append("caseB (runtime pkg resource lookup) FAILED")
    # caseC does not throw when the authority is unknown: getType just returns
    # null, so an unchecked run looks fine. Assert on the returned type instead.
    if "caseC-FAILED" in logs:
        notes.append("caseC (authority) FAILED")
    elif re.search(r"caseC-OK authority=\S+ type=null", logs):
        notes.append("caseC (authority) SILENT FAIL: resolver returned null")
    if "provider-onCreate" not in logs:
        notes.append("provider never created")
    # only meaningful where a native lib actually loaded
    if "JNI-LIB-LOADED" in logs and "caseD nativeHello()" not in logs:
        notes.append("caseD (JNI symbol) BROKEN: lib loaded but native method unresolved")
    if "ClassNotFoundException" in logs or "ClassNotFoundException" in m["crash"]:
        notes.append("ClassNotFoundException")
    return ("PASS" if not notes else "PARTIAL"), notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["a", "b", "c", "d"])
    ap.add_argument("--apk", default=str(HERE / "out" / "fixture.apk"))
    ap.add_argument("--old", default="com.example.oldpkg")
    ap.add_argument("--new", default="com.example.newpkg")
    args = ap.parse_args()

    mode = args.mode
    jar = APKTOOL2 if mode == "d" else APKTOOL3
    work = HERE / "work" / mode
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    decoded = work / "decoded"

    print(f"===== MODE {mode}  (apktool {jar.name})  {args.old} -> {args.new} =====")
    apk = Path(args.apk)
    apktool(jar, ["d", "-f", "-o", str(decoded)], apk)

    # --- inspect what apktool produced, before touching anything
    manifest_text = (decoded / "AndroidManifest.xml").read_text("utf-8")
    m = re.search(r'<manifest[^>]*?\spackage="([^"]+)"', manifest_text)
    print(f"decoded manifest package : {m.group(1) if m else '??'}")
    rel = re.findall(r'android:name="(\.[A-Za-z0-9_.]+)"', manifest_text)
    print(f"relative class names kept: {rel if rel else 'none (apktool expanded them)'}")

    stats = smali_stats(decoded, args.old)
    print(f"smali namespace dirs     : {[str(p.relative_to(decoded)) for p in stats['namespace_dirs']]}")
    print(f"smali L{args.old.replace('.','/')}/ refs : {stats['class_refs']} (of which R$: {stats['r_refs']})")
    print(f"lib/*.so present         : "
          f"{[str(p.relative_to(decoded)) for p in decoded.rglob('*.so')]}")

    extra = []
    if mode == "d":
        # There is no --rename-manifest-package CLI flag in any modern apktool:
        # the string lives in AaptInvoker (an aapt1 flag). The supported route is
        # the apktool.yml key packageInfo.renameManifestPackage, which only takes
        # effect when aapt1 is used.
        set_rename_manifest_package(decoded / "apktool.yml", args.new)
        extra = ["--use-aapt1"]
        man_report = {"note": f"apktool.yml packageInfo.renameManifestPackage={args.new} (+ --use-aapt1); "
                              f"apktool touches nothing else"}
        smali_report = {"note": "no smali changes"}
    else:
        man_report = rewrite_manifest(decoded / "AndroidManifest.xml", args.old, args.new, mode)
        smali_report = rewrite_smali(decoded, args.old, args.new, mode)

    print("--- manifest changes ---")
    for k, v in man_report.items():
        if isinstance(v, list):
            for item in v:
                print(f"    {k}: {item}")
        elif v:
            print(f"    {k}: {v}")
    print("--- smali changes ---")
    for k, v in smali_report.items():
        print(f"    {k}: {v}")

    out_unsigned = work / "unsigned.apk"
    out_aligned = work / "aligned.apk"
    out_signed = work / "signed.apk"
    try:
        build_sign(decoded, jar, out_unsigned, out_aligned, out_signed, extra)
    except SystemExit as e:
        print(f"\n>>> BUILD FAILED: {e}")
        return

    signed_manifest_pkg = re.search(
        r"^package: name='([^']+)'",
        run([str(BUILD_TOOLS / "aapt2"), "dump", "badging", out_signed], check=False).stdout,
        re.M,
    )
    print(f"\nbuilt APK package : {signed_manifest_pkg.group(1) if signed_manifest_pkg else '??'}")
    print(f"built APK size    : {out_signed.stat().st_size:,} bytes")

    run([ADB, "uninstall", args.old], check=False)
    run([ADB, "uninstall", args.new], check=False)
    inst = run([ADB, "install", out_signed], check=False)
    print(f"install           : {inst.stdout.strip().splitlines()[-1] if inst.stdout.strip() else inst.stderr.strip()}")
    if "Success" not in inst.stdout:
        print(">>> INSTALL FAILED, stopping")
        return

    result = measure(args.new)
    verdict, notes = evaluate(result)
    print(f"\nverdict           : {verdict}")
    for n in notes:
        print(f"    ! {n}")
    print("--- logcat ---")
    for line in result["logs"].splitlines():
        if "RenameFixture" in line:
            print("   ", line.split("RenameFixture:", 1)[-1].strip())
    if result["crash"].strip():
        print("--- crash buffer ---")
        print(result["crash"][-1500:])


if __name__ == "__main__":
    main()
