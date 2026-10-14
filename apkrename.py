#!/usr/bin/env python3
"""apkrename.py — đổi package name của một APK đơn, giữ cho APK vẫn cài và chạy được.

Cách dùng:
    python3 apkrename.py duongdan/file.apk
    python3 apkrename.py duongdan/file.apk com.name.moi     # khỏi phải nhập tay
    python3 apkrename.py                                    # hỏi đường dẫn APK
    python3 apkrename.py --help

Nguyên tắc an toàn:
  * Chỉ sửa AndroidManifest.xml. Không đụng vào smali, res/, assets/.
  * Tên class viết tắt (.Foo) được chuyển thành tên đầy đủ theo package CŨ
    trước khi đổi package.
  * Không sửa android:resource / android:value, nên google_app_id và các
    giá trị cấu hình (Firebase, Maps...) giữ nguyên.
  * Không sửa tham chiếu tài nguyên (@package:type/name, ?attr/...): tên
    trong đó có thể là package tài nguyên khác của app (module dynamic
    feature), đổi đi là aapt2 link thất bại.
  * So khớp package theo ranh giới, nên "cct.com.zhiliaoapp.musically"
    (app khác) không bị sửa nhầm.
  * Dừng ngay nếu lib/*.so có hàm JNI gắn với package cũ.
  * APK đóng gói theo dynamic feature (resources.arsc có nhiều package tài
    nguyên) được dựng lại tự động: các tài nguyên mà manifest trỏ sang
    package phụ được sinh lại thành tài nguyên của package chính, rồi
    manifest được trỏ sang tên mới. Dùng --no-restore-res nếu muốn dừng
    thay vì dựng lại.

Chỉ dùng cho APK của chính bạn hoặc APK bạn được phép chỉnh sửa.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
WORK_ROOT = HERE / "work"
KEYSTORE = Path.home() / ".android" / "debug.keystore"
KS_ALIAS = "androiddebugkey"
KS_PASS = "android"

ANDROID_NS = "http://schemas.android.com/apk/res/android"


def A(attr: str) -> str:
    return f"{{{ANDROID_NS}}}{attr}"


# Thẻ mà android:name trỏ tới một class Java -> KHÔNG được sửa, vì class
# vẫn nằm nguyên ở package cũ trong smali.
CLASS_TAGS = {"application", "activity", "activity-alias", "service",
              "receiver", "provider", "instrumentation"}
CLASS_ATTRS = ("name", "targetActivity", "parentActivityName")
# Thuộc tính cấu hình: giữ nguyên (bảo vệ google_app_id, resource id, ...)
SKIP_ATTRS = {"resource", "value"}

# Tham chiếu tài nguyên có ghi rõ package: @com.ten.app:style/abc
# ("@style/abc" không có package là tham chiếu nội bộ, "?attr/x" là theme attr)
FOREIGN_REF = re.compile(r"^@([A-Za-z0-9_.]+):([^/]+)/(.+)$")

# Định dạng output của `aapt2 dump resources`
DUMP_PKG = re.compile(r"^Package name=(\S+) id=([0-9a-f]+)$")
DUMP_TYPE = re.compile(r"^  type (\S+) id=([0-9a-f]+) entryCount=(\d+)$")
# ... resource 0x7f030000 style/AppTheme       <- tài nguyên thường
# ... resource 0x01010054 attr/windowBackground PUBLIC   <- tài nguyên công khai
DUMP_RES = re.compile(r"^    resource 0x([0-9a-f]+) (\S+)/(\S+)(?: [A-Z]+)?$")
DUMP_STYLE = re.compile(r"^\s+\(\) \(style\) size=\d+(?: parent=(\S+) \(0x([0-9a-f]+)\))?$")
DUMP_ITEM = re.compile(r"^\s+0x([0-9a-f]+)=(.*)$")
DUMP_VALUE = re.compile(r"^\s+\(\) (.*)$")

# Nơi apktool cất framework mà nó dùng để link (chứa bảng tên tài nguyên android:*)
FRAMEWORK_DIRS = [
    Path.home() / ".local" / "share" / "apktool" / "framework",
    Path.home() / "Library" / "apktool" / "framework",
    Path.home() / ".apktool" / "framework",
]

JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false", "null",
}

SDK_HINT = (
    "Cài Android SDK build-tools:\n"
    '       sdkmanager "build-tools;35.0.0"\n'
    "       (hoặc đặt ANDROID_HOME trỏ tới thư mục Android SDK)"
)

INSTALL_HINTS = {
    "java": (
        "Cài JDK 17 trở lên:\n"
        "       Debian/Ubuntu : sudo apt install openjdk-17-jdk\n"
        "       Fedora        : sudo dnf install java-17-openjdk-devel\n"
        "       Arch          : sudo pacman -S jdk-openjdk\n"
        "       macOS         : brew install openjdk\n"
        "       Hoặc tải tại  : https://adoptium.net"
    ),
    "apktool": (
        "Tải apktool_*.jar tại https://github.com/iBotPeaches/Apktool/releases\n"
        "       rồi đặt vào thư mục .tools/ cạnh script này,\n"
        "       hoặc cài apktool vào PATH."
    ),
    "zipalign": SDK_HINT,
    "apksigner": SDK_HINT,
}

USAGE = """apkrename — đổi package name cho APK

Cách dùng:
  python3 apkrename.py [tùy chọn] [duongdan.apk] [com.name.moi]

Không truyền đường dẫn thì script sẽ hỏi.
Không truyền package mới thì script sẽ hỏi sau khi giải mã xong.

Tùy chọn:
  --no-restore-res   APK có package tài nguyên phụ (dynamic feature) thì dừng
                     ngay, không tự dựng lại tài nguyên. Mặc định là tự dựng.
  -h, --help         In trợ giúp này.

Chỉ dùng cho APK của chính bạn hoặc APK bạn được phép chỉnh sửa.
"""


# --------------------------------------------------------------------------- in ấn

def step(n: int, title: str) -> None:
    print(f"\n[{n}/7] {title}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


def warn(msg: str) -> None:
    print(f"  [CẢNH BÁO] {msg}")


def die(msg: str) -> None:
    print(f"\n[LỖI] {msg}")
    sys.exit(1)


def die_missing(tool: str) -> None:
    print(f"  [THIẾU] {tool}")
    for line in INSTALL_HINTS[tool].splitlines():
        print(f"       {line}" if not line.startswith(" ") else line)
    print("\n[LỖI] Thiếu công cụ bắt buộc. Cài xong rồi chạy lại.")
    sys.exit(1)


# --------------------------------------------------------------------------- công cụ

def run(cmd, what: str, check: bool = True):
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
        die(f"{what} thất bại.\n{tail}")
    return proc


def run_logged(cmd, what: str, log_path: Path):
    """Như run() nhưng lưu toàn bộ output của công cụ ra file để còn soi khi lỗi."""
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(out, "utf-8")
    except OSError:
        pass
    if proc.returncode != 0:
        die(f"{what} thất bại (mã thoát {proc.returncode}).\n"
            f"     Log đầy đủ: {log_path}\n" + out[-2000:])
    return proc


def find_java() -> str | None:
    java = shutil.which("java")
    if not java:
        return None
    proc = subprocess.run([java, "-version"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    first = (proc.stderr or proc.stdout).splitlines()[0]
    m = re.search(r'"([^"]+)"', first)
    return m.group(1) if m else first.strip()


def find_apktool(java: str) -> tuple[list[str], str] | None:
    """Trả về (lệnh, mô tả). Ưu tiên apktool trên PATH, sau đó .tools/*.jar."""
    on_path = shutil.which("apktool")
    if on_path:
        proc = subprocess.run([on_path, "--version"], capture_output=True, text=True)
        ver = (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.returncode == 0 else "?"
        return [on_path], ver

    jars = sorted((HERE / ".tools").glob("apktool*.jar"),
                  key=lambda p: [int(x) for x in re.findall(r"\d+", p.stem)] or [0])
    if not jars:
        return None
    jar = jars[-1]
    proc = subprocess.run([java, "-jar", str(jar), "--version"],
                          capture_output=True, text=True)
    ver = (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.returncode == 0 else "?"
    return [java, "-jar", str(jar)], f"{ver} ({jar.relative_to(HERE)})"


def sdk_roots() -> list[Path]:
    roots = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    roots += [Path.home() / "Android" / "Sdk",
              Path.home() / "Library" / "Android" / "sdk",
              Path("/usr/lib/android-sdk")]
    return [r for r in roots if r.is_dir()]


def find_build_tool(name: str) -> Path | None:
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    for root in sdk_roots():
        bt = root / "build-tools"
        if not bt.is_dir():
            continue
        versions = sorted((p for p in bt.iterdir() if p.is_dir()),
                          key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)] or [0])
        for v in reversed(versions):
            cand = v / name
            if cand.is_file():
                return cand
    return None


def check_tools() -> dict:
    step(1, "Kiểm tra công cụ")

    java_ver = find_java()
    if not java_ver:
        die_missing("java")
    ok(f"java {java_ver}")

    apktool = find_apktool(shutil.which("java") or "java")
    if not apktool:
        die_missing("apktool")
    ok(f"apktool {apktool[1]}")

    tools = {"apktool": apktool[0]}
    for name in ("zipalign", "apksigner"):
        path = find_build_tool(name)
        if not path:
            die_missing(name)
        ok(f"{name} {path}")
        tools[name] = path
    return tools


# --------------------------------------------------------------------------- APK

def is_apk(path: Path) -> bool:
    if not path.is_file():
        return False
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as z:
        return "AndroidManifest.xml" in z.namelist()


def decode(apktool_cmd: list[str], apk: Path, dest: Path, log_path: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("  Đang giải nén... (APK lớn có thể mất vài phút)")
    run_logged([*apktool_cmd, "d", "-f", "-o", dest, apk], "apktool decode", log_path)


def missing_from_decode(apk: Path, decoded: Path) -> list[str]:
    """File có trong APK mà apktool chưa copy ra.

    lib/ và assets/ được copy nguyên xi nên phải khớp 1-1. Thiếu file nghĩa là
    apktool dừng sớm — và nó vẫn trả về mã thoát 0, nên không thể tin mã thoát.
    Bỏ qua bước này thì build sẽ đổ với lỗi khó hiểu kiểu
    'aapt2: error: file not found', và tệ hơn là bước kiểm tra JNI ở bước 4
    sẽ bỏ sót toàn bộ lib/*.so.
    """
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
    return [n for n in names
            if not n.endswith("/")
            and n.startswith(("lib/", "assets/"))
            and not (decoded / n).is_file()]


def read_package(decoded: Path) -> str | None:
    """Đọc package từ manifest đã decode (không dùng regex trên cả file)."""
    try:
        root = ET.parse(decoded / "AndroidManifest.xml").getroot()
    except ET.ParseError:
        return None
    return root.get("package")


def find_foreign_resource_refs(manifest: Path, old: str) -> list[tuple[str, str, str]]:
    """Tham chiếu tài nguyên trỏ ra ngoài package của app.

    APK đóng gói theo dynamic feature (bản tải từ Google Play) có NHIỀU package
    tài nguyên trong resources.arsc. apktool chỉ giải mã được package chính,
    nhưng vẫn để nguyên trong manifest những tham chiếu kiểu
    @com.ten.app.df_kakao:style/a. Lúc build, apktool gọi
    `aapt2 link --package-id 127` nên các package phụ không tồn tại, tham chiếu
    không resolve được, build đổ với "failed processing manifest".

    Đo trên TikTok 43.8.3: 3 package tài nguyên (0x7f app + 0x6c df_kakao
    + 0x6d df_line), apktool giải mã 71.915/71.921 tài nguyên, bỏ rơi 6 cái
    của 2 package phụ, manifest còn 2 tham chiếu vào đó. Giải mã rồi build lại
    KHÔNG sửa gì vẫn hỏng y hệt.

    Phát hiện ở đây để dừng ngay, thay vì bắt người dùng chờ hết pipeline.

    Trả về [(thẻ, thuộc tính, giá trị)], đã khử trùng lặp. Bỏ qua package
    "android" vì framework luôn có sẵn.
    """
    try:
        root = ET.parse(manifest).getroot()
    except ET.ParseError:
        return []
    hits, seen = [], set()
    for el in root.iter():
        for key, val in el.attrib.items():
            m = FOREIGN_REF.match(val or "")
            if not m or m.group(1) in (old, "android"):
                continue
            item = (tag_of(el), key.rsplit("}", 1)[-1], val)
            if item not in seen:
                seen.add(item)
                hits.append(item)
    return hits


# --------------------------------------------------------------------------- JNI

def find_jni_conflicts(decoded: Path, old: str) -> list[tuple[str, list[str]]]:
    """Tìm .so có symbol Java_<package cũ>_... (JNI đăng ký tĩnh).

    Trả về [(đường dẫn .so, [các symbol])]. Tên symbol thường xuất hiện nhiều
    lần trong một file (bảng .dynstr và .strtab), nên phải khử trùng lặp.
    """
    needle = b"Java_" + old.replace(".", "_").encode() + b"_"
    found = []
    for so in sorted(decoded.rglob("*.so")):
        try:
            blob = so.read_bytes()
        except OSError:
            continue
        if needle not in blob:
            continue
        syms = sorted({m.group(0).decode("ascii", "replace")
                       for m in re.finditer(rb"Java_[A-Za-z0-9_]+", blob)
                       if m.group(0).startswith(needle)})
        if syms:
            found.append((str(so.relative_to(decoded)), syms))
    return found


# ------------------------------------------------------- khôi phục tài nguyên package phụ

class RestoreError(Exception):
    """Không dựng lại được một tài nguyên của package phụ."""


def parse_resource_dump(lines) -> dict[int, dict]:
    """Đọc output của `aapt2 dump resources` thành {id: bản ghi}.

    Định dạng:
        Package name=com.ten.app id=7f
          type style id=03 entryCount=2
            resource 0x7f030000 style/AppTheme
              () (style) size=3 parent=android:style/Theme (0x01030000)
                0x01010054=@0x0106000d
            resource 0x7f040001 string/hi
              () "xin chào"

    Một tài nguyên có thể có nhiều mục config (ban đêm, màn hình rộng...); chỉ
    mục đầu được ghi lại, đủ dùng cho việc dựng lại style.
    """
    out: dict[int, dict] = {}
    pkg = typ = None
    cur = None
    for line in lines:
        m = DUMP_PKG.match(line)
        if m:
            pkg = m.group(1)
            continue
        m = DUMP_TYPE.match(line)
        if m:
            typ = m.group(1)
            continue
        m = DUMP_RES.match(line)
        if m:
            cur = {"id": int(m.group(1), 16), "pkg": pkg, "type": typ,
                   "name": m.group(3), "kind": None, "parent": None,
                   "items": [], "value": None}
            out[cur["id"]] = cur
            continue
        if cur is None:
            continue
        m = DUMP_STYLE.match(line)
        if m:
            cur["kind"] = "style"
            if m.group(2):
                cur["parent"] = int(m.group(2), 16)
            continue
        if cur["kind"] == "style":
            m = DUMP_ITEM.match(line)
            if m:
                cur["items"].append((int(m.group(1), 16), m.group(2)))
                continue
        if cur["kind"] is None:
            m = DUMP_VALUE.match(line)
            if m:
                val = m.group(1)
                if val.startswith("(file)"):
                    cur["kind"], cur["value"] = "file", val
                elif val.startswith("(style)"):
                    cur["kind"] = "style"
                else:
                    cur["kind"], cur["value"] = "value", val
    return out


def try_dump_resources(aapt2: Path, target: Path) -> dict[int, dict] | None:
    """Dump bảng tài nguyên của một APK/jar. Trả None nếu aapt2 không đọc được."""
    proc = subprocess.run([str(aapt2), "dump", "resources", str(target)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return parse_resource_dump(proc.stdout.splitlines())


class FrameworkNames:
    """Tra ID tài nguyên của framework thành (type, name), ví dụ 0x01010054.

    Nạp lười từng nguồn một: ưu tiên framework mà apktool dùng để link (đúng
    bảng mà bước build sẽ tra), sau đó tới android.jar của các platform.
    """

    def __init__(self, aapt2: Path):
        self.aapt2 = aapt2
        self.sources = self._candidates()
        self.maps: list[dict] = []
        self.loaded = 0

    @staticmethod
    def _candidates() -> list[Path]:
        """Nơi có thể có bảng tên tài nguyên android:*, xếp theo thứ tự thử.

        Ưu tiên framework của apktool (nhỏ, đúng bản apktool dùng để link), rồi
        tới android.jar của SDK — mới nhất trước.
        """
        out: list[Path] = []
        seen: set[Path] = set()

        def add(paths) -> None:
            for p in paths:
                real = p.resolve()
                if real not in seen:
                    seen.add(real)
                    out.append(real)

        for d in FRAMEWORK_DIRS:
            if d.is_dir():
                add(sorted(p for p in d.glob("*.apk") if p.is_file()))
        for root in sdk_roots():
            plats = sorted((p for p in (root / "platforms").glob("android-*") if p.is_dir()),
                           key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)] or [0])
            add([p / "android.jar" for p in reversed(plats) if (p / "android.jar").is_file()])
        return out

    def get(self, rid: int) -> tuple[str, str] | None:
        while True:
            for m in self.maps:
                rec = m.get(rid)
                if rec:
                    return rec["type"], rec["name"]
            if self.loaded >= len(self.sources):
                return None
            found = try_dump_resources(self.aapt2, self.sources[self.loaded])
            self.loaded += 1
            if found is not None:
                self.maps.append(found)


class Restorer:
    """Dựng lại tài nguyên của package phụ vào package chính của app.

    apktool chỉ giải mã package tài nguyên chính, nên tham chiếu trỏ vào package
    phụ (module dynamic feature) không resolve được lúc aapt2 link. Ở đây đọc
    định nghĩa gốc của chúng rồi sinh ra tài nguyên mới trong package chính, tên
    khác, và trỏ manifest sang tên mới.

    Chỉ dựng lại được tài nguyên dạng style — đó cũng là thứ manifest hay trỏ
    tới nhất (android:theme). Gặp dạng khác thì báo lỗi rõ ràng, không đoán.
    """

    def __init__(self, app_pkg: str, res: dict[int, dict], fw: FrameworkNames):
        self.app_pkg = app_pkg
        self.res = res
        self.fw = fw
        self.by_sym = {(r["pkg"], r["type"], r["name"]): i for i, r in res.items()}
        self.new_names: dict[int, str] = {}
        self.styles: list[str] = []
        self.taken = {r["name"] for r in res.values() if r["pkg"] == app_pkg}

    # -- đặt tên mới, tránh đụng tài nguyên có sẵn của app -------------------
    def _new_name(self, rec: dict) -> str:
        base = re.sub(r"[^a-z0-9_]", "_",
                      f"{rec['pkg'].rsplit('.', 1)[-1]}_{rec['name']}".lower())
        name, i = base, 2
        while name in self.taken:
            name, i = f"{base}_{i}", i + 1
        self.taken.add(name)
        self.new_names[rec["id"]] = name
        return name

    def name_for(self, rid: int, stack: tuple) -> str:
        """Tên mới trong package chính của tài nguyên `rid`, dựng lại nếu chưa có."""
        if rid in self.new_names:
            return self.new_names[rid]
        rec = self.res.get(rid)
        if rec is None:
            raise RestoreError(f"không có tài nguyên 0x{rid:x} trong bảng gốc")
        if rec["kind"] != "style":
            raise RestoreError(
                f"{rec['pkg']}:{rec['type']}/{rec['name']} là dạng "
                f"'{rec['kind'] or 'không rõ'}', chỉ dựng lại được style")
        if rid in stack:
            raise RestoreError(f"tài nguyên 0x{rid:x} tham chiếu vòng")
        name = self._new_name(rec)
        self.styles.append(self._render_style(rec, name, stack + (rid,)))
        return name

    def value(self, val: str, pkg: str, stack: tuple) -> str:
        """Dịch một giá trị trong style gốc sang dạng aapt2 hiểu được."""
        val = val.strip()
        if val in ("@null", "true", "false"):
            return val
        m = re.fullmatch(r"@0x([0-9a-f]+)", val)
        if m:
            rid = int(m.group(1), 16)
            rec = self.res.get(rid)
            if rec is not None:
                if rec["pkg"] == self.app_pkg:
                    # tài nguyên của chính app -> tham chiếu nội bộ, khỏi package
                    return f"@{rec['type']}/{rec['name']}"
                return f"@{rec['type']}/{self.name_for(rid, stack)}"
            fw = self.fw.get(rid)
            if fw:
                return f"@android:{fw[0]}/{fw[1]}"
            raise RestoreError(f"không tra được tài nguyên 0x{rid:x}")
        m = re.fullmatch(r"@([A-Za-z0-9_]+)/(\S+)", val)
        if m:
            rid = self.by_sym.get((pkg, m.group(1), m.group(2)))
            if rid is not None:
                return f"@{m.group(1)}/{self.name_for(rid, stack)}"
        return val  # số, màu, chuỗi... giữ nguyên

    def _attr_name(self, attr_id: int) -> str:
        rec = self.res.get(attr_id)
        if rec is not None:
            if rec["pkg"] == self.app_pkg:
                return rec["name"]
            raise RestoreError(
                f"attr {rec['pkg']}:{rec['name']} thuộc package phụ, chưa dựng lại được")
        fw = self.fw.get(attr_id)
        if not fw:
            raise RestoreError(f"không tra được tên attr 0x{attr_id:x}")
        return f"android:{fw[1]}"

    def _render_style(self, rec: dict, name: str, stack: tuple) -> str:
        parent = ""
        if rec["parent"]:
            parent = f' parent="{self.value(f"@0x{rec["parent"]:x}", rec["pkg"], stack)}"'
        out = [f'    <style name="{name}"{parent}>']
        for attr_id, val in rec["items"]:
            out.append(f'        <item name="{self._attr_name(attr_id)}">'
                       f'{self.value(val, rec["pkg"], stack)}</item>')
        out.append("    </style>")
        return "\n".join(out)

    def restore(self, refs: list[tuple[str, str, str]]) -> dict[str, str]:
        """refs: [(thẻ, thuộc tính, giá trị)] lấy từ manifest.

        Trả về {giá trị cũ trong manifest: giá trị mới}.
        """
        mapping = {}
        for _, _, val in refs:
            m = FOREIGN_REF.match(val)
            rid = self.by_sym.get((m.group(1), m.group(2), m.group(3)))
            if rid is None:
                raise RestoreError(
                    f"không tìm thấy {m.group(2)}/{m.group(3)} trong package {m.group(1)}")
            mapping[val] = f"@{m.group(2)}/{self.name_for(rid, ())}"
        return mapping

    def write(self, res_dir: Path) -> Path:
        """Ghi các style đã dựng lại thành một file values riêng."""
        path = res_dir / "values" / "apkrename_restored.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = ('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                + "\n".join(self.styles) + "\n</resources>\n")
        path.write_text(body, "utf-8")
        return path


def find_aapt2() -> Path | None:
    return find_build_tool("aapt2")


def restore_foreign_resource_refs(apk: Path, decoded: Path, old: str,
                                  foreign: list[tuple[str, str, str]],
                                  work: Path) -> dict[str, str]:
    """Dựng lại tài nguyên package phụ vào package chính của app.

    Trả về {giá trị cũ trong manifest: giá trị mới}. Dừng cả chương trình nếu
    không dựng lại được, vì để nguyên thì build chắc chắn thất bại.
    """
    aapt2 = find_aapt2()
    if not aapt2:
        shutil.rmtree(work, ignore_errors=True)
        die(f"Cần aapt2 để dựng lại tài nguyên package phụ nhưng không tìm thấy.\n"
            f"     {SDK_HINT}")

    print("    Đang đọc bảng tài nguyên của APK gốc...")
    res = try_dump_resources(aapt2, apk)
    if res is None:
        shutil.rmtree(work, ignore_errors=True)
        die("aapt2 không đọc được bảng tài nguyên của APK gốc.")

    restorer = Restorer(old, res, FrameworkNames(aapt2))
    try:
        mapping = restorer.restore(foreign)
    except RestoreError as e:
        shutil.rmtree(work, ignore_errors=True)
        die(f"Không dựng lại được tài nguyên package phụ: {e}\n"
            f"     APK này cần sửa tay, xem mục \"APK có package tài nguyên phụ\"\n"
            f"     trong README.")

    path = restorer.write(decoded / "res")
    ok(f"Đã dựng lại {len(restorer.styles)} tài nguyên vào package chính "
       f"-> {path.parent.parent.name}/{path.parent.name}/{path.name}")
    for old_val, new_val in mapping.items():
        info(f"{old_val}  ->  {new_val}")

    extra = sum(1 for r in res.values()
                if r["pkg"] not in (old, "android")
                and r["id"] not in restorer.new_names)
    if extra:
        info(f"Package phụ còn {extra} tài nguyên khác không dựng lại "
             f"(manifest không trỏ tới).")
    return mapping


# --------------------------------------------------------------------------- package mới

def parse_package(pkg: str) -> tuple[bool, str]:
    """Trả về (hợp lệ, lý do). Mỗi đoạn bắt đầu bằng chữ cái, ít nhất 2 đoạn."""
    pkg = pkg.strip()
    if not pkg:
        return False, "không được để trống"
    if " " in pkg:
        return False, "không được có khoảng trắng"
    segments = pkg.split(".")
    if len(segments) < 2:
        return False, "phải có ít nhất 2 đoạn, ví dụ com.ten.app"
    for seg in segments:
        if not seg:
            return False, "có đoạn rỗng (dấu chấm đứng liền nhau?)"
        if not seg[0].isalpha() or not seg[0].isascii():
            return False, f'đoạn "{seg}" phải bắt đầu bằng chữ cái'
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", seg):
            return False, f'đoạn "{seg}" chỉ được chứa chữ, số và dấu gạch dưới'
    return True, ""


def ask_package(old: str) -> str:
    step(5, "Nhập package mới")
    info(f"Package hiện tại: {old}")
    while True:
        try:
            raw = input("  Nhập package mới: ")
        except EOFError:
            die("Không đọc được package mới.")
        pkg = raw.strip()
        valid, reason = parse_package(pkg)
        if not valid:
            warn(f'"{pkg}" không hợp lệ: {reason}. Nhập lại.')
            continue
        if pkg == old:
            warn("Package mới trùng package cũ. Nhập lại.")
            continue
        keywords = [s for s in pkg.split(".") if s in JAVA_KEYWORDS]
        if keywords:
            warn(f'đoạn {", ".join(keywords)} trùng từ khóa Java; '
                 "một số công cụ build có thể từ chối.")
            if input("  Vẫn dùng? (c/K): ").strip().lower() != "c":
                continue
        return pkg


# --------------------------------------------------------------------------- manifest

def root_tag(text: str) -> str:
    m = re.search(r"<manifest\b[^>]*>", text)
    return m.group(0) if m else text[:2000]


def tag_of(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if "}" in el.tag else el.tag


def name_of(el) -> str:
    """android:name của một phần tử, để in ra cho dễ đối chiếu."""
    return el.get(A("name")) or "?"


def replace_pkg(value: str, old: str, new: str) -> str:
    """Thay 'old' -> 'new' nhưng chỉ ở ranh giới package.

    'cct.com.zhiliaoapp.musically' KHÔNG khớp vì ký tự trước là dấu chấm.
    'com.x.y.fileprovider' khớp (theo sau là dấu chấm).
    """
    pattern = re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(old) + r"(?![A-Za-z0-9_])")
    return pattern.sub(new, value)


def rewrite_manifest(path: Path, old: str, new: str,
                     res_map: dict[str, str] | None = None) -> dict:
    text = path.read_text("utf-8")
    for m in re.finditer(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', root_tag(text)):
        ET.register_namespace(m.group(1), m.group(2))
    root = ET.fromstring(text)

    report = {"relative": [], "identifiers": [], "kept_class": [],
              "kept_config": [], "kept_res": [], "restored": [],
              "authorities": [], "permissions": []}

    # (1) tên class viết tắt -> đầy đủ theo package CŨ, phải làm TRƯỚC khi đổi package
    for el in root.iter():
        if tag_of(el) not in CLASS_TAGS:
            continue
        for attr in CLASS_ATTRS:
            key, val = A(attr), el.get(A(attr))
            if val and val.startswith("."):
                el.set(key, old + val)
                report["relative"].append(f"{tag_of(el)}/{attr}: {val} -> {old + val}")

    # (2) thuộc tính package của <manifest>
    root.set("package", new)

    # (3) các thuộc tính còn lại có chứa package
    for el in root.iter():
        tag = tag_of(el)
        for key, val in list(el.attrib.items()):
            if key == "package" or not val or old not in val:
                continue
            name = key.rsplit("}", 1)[-1]
            if name in SKIP_ATTRS:
                report["kept_config"].append(f"{tag}/{name} = {val}")
                continue
            # Tham chiếu tài nguyên (@package:type/name, ?attr/...) phải giữ
            # nguyên: tên trong đó có thể là package tài nguyên KHÁC của app
            # (các module dynamic feature), không phải package của app.
            # Ngoại lệ: tham chiếu đã được dựng lại thành tài nguyên của app.
            if val.startswith(("@", "?")):
                if res_map and val in res_map:
                    el.set(key, res_map[val])
                    report["restored"].append(f"{tag}/{name}: {val} -> {res_map[val]}")
                else:
                    report["kept_res"].append(f"{tag}/{name} = {val}")
                continue
            if tag in CLASS_TAGS and name in CLASS_ATTRS:
                report["kept_class"].append(f"{tag}/{name} = {val}")
                continue
            newval = replace_pkg(val, old, new)
            if newval != val:
                el.set(key, newval)
                report["identifiers"].append(f"{tag}/{name}: {val} -> {newval}")

    # (4) android:authorities phải DUY NHẤT trên toàn máy, không chỉ trong app.
    # Authority nào không chứa package mới — hằng số của SDK, ví dụ
    # "com.facebook.app.FacebookContentProvider123" hay
    # "com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233" — thì
    # bản đổi tên giữ nguyên y hệt bản gốc và cài song song sẽ đổ ngay:
    #   INSTALL_FAILED_CONFLICTING_PROVIDER: ... is already used by <app gốc>
    # Đây là lỗi cài đặt, không phải lỗi chạy, nên phải sửa chứ không chỉ cảnh báo.
    for el in root.iter():
        key = A("authorities")
        val = el.get(key)
        if not val:
            continue
        parts = [p.strip() for p in val.split(";")]
        fixed = [p if not p or new in p else f"{p}.{new}" for p in parts]
        newval = ";".join(fixed)
        if newval != val:
            el.set(key, newval)
            report["authorities"].append(
                f"{tag_of(el)}/{name_of(el)}: {val} -> {newval}")

    # (5) <permission android:name> cũng phải duy nhất trên toàn máy: trùng tên
    # với bản gốc nhưng khác chữ ký -> INSTALL_FAILED_DUPLICATE_PERMISSION.
    # Không tự sửa, vì app khác có thể đang xin đúng tên quyền đó; chỉ báo.
    for el in root.iter():
        if tag_of(el) != "permission":
            continue
        nm = el.get(A("name"))
        if nm and new not in nm:
            report["permissions"].append(nm)

    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                    + ET.tostring(root, encoding="unicode") + "\n", "utf-8")
    return report


# --------------------------------------------------------------------------- build

def ensure_keystore() -> None:
    if KEYSTORE.is_file():
        ok(f"Keystore: {KEYSTORE}")
        return
    KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Chưa có keystore debug, đang tạo: {KEYSTORE}")
    run(["keytool", "-genkeypair", "-v", "-keystore", KEYSTORE,
         "-alias", KS_ALIAS, "-storepass", KS_PASS, "-keypass", KS_PASS,
         "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
         "-dname", "CN=Android Debug,O=Android,C=US"], "keytool genkeypair")
    ok("Đã tạo keystore debug")


def build_sign(tools: dict, decoded: Path, work: Path, out: Path) -> None:
    unsigned, aligned = work / "unsigned.apk", work / "aligned.apk"

    print("  Đang build lại...")
    run_logged([*tools["apktool"], "b", "-o", unsigned, decoded],
               "apktool build", work / "apktool-build.log")

    print("  Đang zipalign...")
    run([tools["zipalign"], "-f", "-p", "4", unsigned, aligned], "zipalign")

    print("  Đang ký...")
    out.parent.mkdir(parents=True, exist_ok=True)
    run([tools["apksigner"], "sign", "--ks", KEYSTORE, "--ks-pass", f"pass:{KS_PASS}",
         "--key-pass", f"pass:{KS_PASS}", "--ks-key-alias", KS_ALIAS,
         "--out", out, aligned], "apksigner sign")


# --------------------------------------------------------------------------- main

def main() -> None:
    restore_res = True
    positional = []
    for arg in sys.argv[1:]:
        if arg in ("-h", "--help"):
            print(USAGE)
            return
        if arg == "--no-restore-res":
            restore_res = False
        elif arg.startswith("-"):
            die(f"Tham số lạ: {arg}\n     Xem: python3 apkrename.py --help")
        else:
            positional.append(arg)

    if positional:
        apk_arg = positional[0]
    else:
        try:
            apk_arg = input("Nhập đường dẫn APK: ").strip().strip("'\"")
        except EOFError:
            die("Không đọc được đường dẫn APK.")
    apk = Path(apk_arg).expanduser().resolve()

    pre_pkg = positional[1].strip() if len(positional) > 1 else None

    print("=" * 64)
    print(" apkrename — đổi package name cho APK")
    print(" Chỉ dùng cho APK của bạn hoặc APK bạn được phép chỉnh sửa.")
    print("=" * 64)

    tools = check_tools()

    step(2, "Kiểm tra file APK")
    if not apk.is_file():
        die(f"Không tìm thấy file: {apk}")
    if not is_apk(apk):
        die(f"Không phải APK hợp lệ (thiếu AndroidManifest.xml): {apk}")
    ok(f"{apk.name}  ({apk.stat().st_size:,} bytes)")

    work = WORK_ROOT / (re.sub(r"[^A-Za-z0-9_.-]", "_", apk.stem) or "apk")
    decoded = work / "decoded"

    step(3, "Giải nén và tìm package hiện tại")
    decode(tools["apktool"], apk, decoded, work / "apktool-decode.log")
    missing = missing_from_decode(apk, decoded)
    if missing:
        die(f"apktool decode dừng sớm: thiếu {len(missing)} file so với APK gốc.\n"
            f"     Ví dụ: {', '.join(missing[:3])}\n"
            f"     Log: {work / 'apktool-decode.log'}\n"
            f"     Xoá thư mục này rồi chạy lại: {work}")
    old = read_package(decoded)
    if not old:
        die(f"Không tìm thấy thuộc tính package trong AndroidManifest.xml.\n"
            f"     Thư mục decode giữ lại để xem: {decoded}")
    ok(f"Tìm thấy package: {old}")

    step(4, "Kiểm tra có đổi được an toàn không")

    # Quét .so trước vì rẻ hơn dựng lại tài nguyên; chặn sớm thì đỡ mất công.
    hits = find_jni_conflicts(decoded, old)
    if hits:
        pairs = [(so, sym) for so, syms in hits for sym in syms]
        print("  [CHẶN] KHÔNG đổi được an toàn.")
        print(f"    Tìm thấy {len(pairs)} hàm JNI gắn với package cũ "
              f"trong {len(hits)} thư viện native:")
        for so, sym in pairs[:5]:
            print(f"      {so}  ->  {sym}")
        if len(pairs) > 5:
            print(f"      ... và {len(pairs) - 5} chỗ khác")
        print("    Đổi package sẽ làm các hàm native này không tìm thấy lớp Java,")
        print("    app sẽ crash khi gọi tới.")
        shutil.rmtree(work, ignore_errors=True)
        sys.exit(1)
    ok("Không có thư viện native nào gắn với package cũ")

    res_map: dict[str, str] = {}
    foreign = find_foreign_resource_refs(decoded / "AndroidManifest.xml", old)
    if foreign:
        warn("APK đóng gói theo dynamic feature: resources.arsc có nhiều package")
        print("    tài nguyên, apktool chỉ giải mã được package chính.")
        print(f"    Manifest có {len(foreign)} tham chiếu trỏ sang package phụ:")
        for tag, attr, val in foreign[:8]:
            print(f"      {tag}/{attr} = {val}")
        if len(foreign) > 8:
            print(f"      ... và {len(foreign) - 8} chỗ khác")
        if not restore_res:
            print("    Để nguyên thì aapt2 link sẽ đổ ở bước build:")
            print("      error: resource <package phụ>:style/... not found")
            print("      error: failed processing manifest")
            print("    Cách xử lý: bỏ --no-restore-res để script tự dựng lại, hoặc")
            print("    dùng bản APK đơn (APK cài trực tiếp, không phải bản tải từ")
            print("    Google Play).")
            shutil.rmtree(work, ignore_errors=True)
            sys.exit(1)
        res_map = restore_foreign_resource_refs(apk, decoded, old, foreign, work)
    else:
        ok("Manifest không tham chiếu ra ngoài package của app")

    if pre_pkg:
        step(5, "Package mới")
        valid, reason = parse_package(pre_pkg)
        if not valid:
            shutil.rmtree(work, ignore_errors=True)
            die(f'"{pre_pkg}" không hợp lệ: {reason}')
        new = pre_pkg
        info(f"{old}  ->  {new}")
    else:
        new = ask_package(old)

    step(6, "Đổi tên, build lại, zipalign, ký")
    report = rewrite_manifest(decoded / "AndroidManifest.xml", old, new, res_map)
    info(f"package: {old} -> {new}")
    for key, label in (("relative", "tên class viết tắt -> đầy đủ"),
                       ("identifiers", "giá trị chứa package"),
                       ("restored", "tham chiếu tài nguyên đã dựng lại"),
                       ("authorities", "provider authority phải đổi để cài song song"),
                       ("kept_class", "giữ nguyên tên class"),
                       ("kept_res", "giữ nguyên tham chiếu tài nguyên"),
                       ("kept_config", "giữ nguyên cấu hình")):
        items = report[key]
        if items:
            print(f"  {label} ({len(items)}):")
            for it in items[:6]:
                print(f"    - {it}")
            if len(items) > 6:
                print(f"    ... và {len(items) - 6} chỗ khác")
    if not report["identifiers"]:
        warn("Không có thuộc tính nào khác chứa package cũ.")
    if report["permissions"]:
        warn(f"{len(report['permissions'])} quyền khai báo không chứa package "
             f"({', '.join(report['permissions'][:3])}): cài song song có thể đổ "
             f"với INSTALL_FAILED_DUPLICATE_PERMISSION.")

    ensure_keystore()
    out = OUTPUT_DIR / f"{apk.stem}_{new}.apk"
    build_sign(tools, decoded, work, out)

    verify = run([tools["apksigner"], "verify", "--verbose", out],
                 "apksigner verify", check=False)
    if verify.returncode == 0:
        ok("Chữ ký hợp lệ")
    else:
        warn("apksigner verify báo lỗi, kiểm tra lại file.")

    step(7, "Kết quả")
    print(f"\n  Xong! File: {out}")
    print(f"  Kích thước: {out.stat().st_size:,} bytes")
    print(f"\n  Cài thử:")
    print(f"    adb install -r \"{out}\"")
    print(f"\n  Lưu ý: APK này được ký bằng keystore debug, không cập nhật đè lên")
    print(f"  bản cài sẵn của {old} được (khác chữ ký). Nếu giữ nguyên package cũ,")
    print(f"  phải gỡ bản cũ trước. Nếu đổi sang package mới thì cài song song được.")

    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã hủy.")
        sys.exit(130)
