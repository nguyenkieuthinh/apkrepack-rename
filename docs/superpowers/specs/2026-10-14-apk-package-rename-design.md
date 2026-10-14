# Thiết kế: công cụ đổi package name của APK

Ngày: 2026-10-14
Trạng thái: chờ duyệt
Kết quả đo liên quan: `docs/experiments/2026-10-14-rename-modes.md`

## 1. Mục đích và phạm vi

Công cụ dòng lệnh nhận một APK cùng package cũ và package mới, đổi tên package, rồi
xuất ra một APK **cài được và chạy được**.

Chỉ dùng cho APK của chính người dùng hoặc APK đã được phép chỉnh sửa. Câu này xuất
hiện ở đầu `README.md` và ở `--help`.

**Ngoài phạm vi** (ghi rõ trong README, không cố xử lý):

- Split APK / App Bundle nhiều APK: cần gộp hoặc xử lý riêng từng phần.
- XAPK / APKS / APKM: là các định dạng đóng gói, phải bóc tách trước.
- AAB: là định dạng build, không phải định dạng cài đặt.
- APK có chữ ký gốc cần giữ: không thể, vì không có private key. Mọi APK xuất ra đều
  được ký lại bằng key debug.

## 2. Cơ sở thực nghiệm cho các quyết định

Đã đo 4 chế độ trên fixture thật, cài lên máy thật, đọc logcat
(`docs/experiments/2026-10-14-rename-modes.md`). Ba kết quả định hình thiết kế:

1. **apktool không sinh lại class `R`.** Class `R` decode được từ APK gốc vẫn được
   biên dịch nguyên vào dex, kể cả khi thuộc tính `package` đã đổi. Vì vậy **chỉ sửa
   manifest là đủ** cho app thường, không cần đụng smali.
2. **`--rename-manifest-package` không còn là CLI option** ở apktool 2.8.1 / 2.9.3 /
   2.12.1 / 3.0.3. Chạy được qua `apktool.yml` + `--use-aapt1` thì để lại bảng tài
   nguyên chưa đổi và hỏng âm thầm. Không dùng.
3. **Di chuyển namespace smali phá JNI**, đã đo được `UnsatisfiedLinkError`. Phải
   chặn cứng khi APK có symbol `Java_<package-cũ>_`.

Suy ra: mặc định là **chỉ sửa manifest**; việc di chuyển smali là cờ riêng, có chặn.

## 3. Kiến trúc

Package Python, chỉ dùng thư viện chuẩn, chạy bằng `python3 -m apkrename`.

| Module | Trách nhiệm | Phụ thuộc |
|---|---|---|
| `__main__.py` | điểm vào | `cli` |
| `cli.py` | phân tích tham số, điều phối 7 bước, in tiến trình, ánh xạ lỗi sang exit code | tất cả |
| `toolchain.py` | dò `java`, `apktool`, `zipalign`, `apksigner`, `adb`, `keytool`; dò build-tools trong SDK | không |
| `packagename.py` | validate tên package; cảnh báo từ khoá Java | không |
| `manifest.py` | sửa `AndroidManifest.xml` | không |
| `references.py` | quét & sửa smali/res; phát hiện namespace; phát hiện JNI | không |
| `build.py` | gọi apktool decode/build, zipalign, keytool, apksigner | `toolchain` |

Nguyên tắc tách: `packagename`, `manifest`, `references` là **hàm thuần trên chuỗi và
cây thư mục** — test được không cần APK, không cần thiết bị. Chỉ `build.py` và `cli.py`
mới gọi subprocess.

### Giao diện dòng lệnh

```
python3 -m apkrename <apk> <package-cũ> <package-mới> [options]
```

| Option | Mặc định | Ý nghĩa |
|---|---|---|
| `--output-dir DIR` | `output/` | nơi ghi APK kết quả |
| `--keystore PATH` | `keys/debug.keystore` | tạo bằng `keytool` nếu chưa có |
| `--ks-alias` / `--ks-pass` / `--key-pass` | `androiddebugkey` / `android` / `android` | |
| `--rewrite-smali` | tắt | di chuyển cả cây namespace smali |
| `--rewrite-strings` | tắt | thay cả literal chuỗi chứa package cũ trong smali |
| `--rewrite-res` | tắt | thay cả trong `res/` |
| `--force` | tắt | bỏ qua chặn cứng JNI (in cảnh báo đậm) |
| `--dry-run` | tắt | decode, in kế hoạch thay đổi, không sửa gì |
| `--check` | tắt | chỉ chạy preflight rồi thoát |
| `--keep-work` | tắt | giữ thư mục `work/` |
| `--yes` | tắt | không dừng ở các cảnh báo cần xác nhận (mục 6) |
| `--apktool PATH` `--java PATH` `--sdk-root DIR` | tự dò | ghi đè đường dẫn công cụ |
| `-v` / `-q` | | tăng/giảm mức in; `-v` stream output của subprocess |

## 4. Quy trình 7 bước

1. **Preflight** — dò toàn bộ công cụ, **báo tất cả cái thiếu cùng lúc** rồi thoát
   code 2. Thứ tự dò `zipalign`/`apksigner`: `PATH` → `$ANDROID_HOME` →
   `$ANDROID_SDK_ROOT` → `~/Android/Sdk/build-tools/*` (Linux) /
   `%LOCALAPPDATA%\Android\Sdk\build-tools\*` (Windows); chọn bản cao nhất theo so
   sánh số, không so chuỗi (để `36.0.0` > `9.0.0`). `apktool` thiếu → in lệnh cài
   (gói distro, hoặc tải `apktool_<ver>.jar` + script bọc vào `~/.local/bin`), không
   tự tải.
2. **Validate** — APK tồn tại, là file, mở được như zip. Tên package cũ và mới hợp lệ
   theo `packagename.py`. Bước này chưa đọc được manifest nên **chưa** kiểm tra được
   package cũ có khớp thật không.
3. **Decode** — `apktool d -f -o <work>/decoded <apk>`. Giữ nguyên output của apktool
   khi lỗi.
4. **Rewrite** — bắt đầu bằng việc đọc thuộc tính `package` trong manifest vừa decode
   và **đối chiếu với package cũ truyền vào**; lệch nhau thì báo lỗi rõ (exit 3) kèm
   giá trị thật đọc được, để người dùng sửa tham số. Sau đó áp dụng mục 5 và 6.
5. **Build** — `apktool b -o <work>/unsigned.apk <work>/decoded`.
6. **Zipalign** — `zipalign -f -p 4`, **trước** khi ký.
7. **Ký và kiểm tra** — tạo keystore nếu chưa có, `apksigner sign` (v1+v2+v3), rồi
   `apksigner verify --print-certs` để xác nhận. In lệnh `adb install`.

## 5. Quy tắc thay thế

### 5.1 Manifest

Quy tắc an toàn chung: **chỉ thay giá trị có chứa literal package cũ**, không bao giờ
thay theo tên attribute.

Thực hiện theo thứ tự:

1. **Mở rộng tên class tương đối trước khi đổi package.** `.RelativeActivity` →
   `com.example.oldpkg.RelativeActivity`. Đã kiểm chứng apktool 3.0.3 **giữ nguyên**
   tên tương đối khi decode, nên bước này là bắt buộc: nếu bỏ qua, aapt2 sẽ hiểu
   `.RelativeActivity` là `com.example.newpkg.RelativeActivity` trong khi smali vẫn
   nằm ở package cũ. Áp dụng cho: `application`, `activity`, `activity-alias`,
   `service`, `receiver`, `provider`, `instrumentation`; các attribute `android:name`,
   `android:targetActivity`, `android:parentActivityName`.
2. **Đổi thuộc tính `package` ở root.**
3. **Thay các giá trị dạng định danh** có chứa package cũ: `android:authorities`
   (kể cả danh sách ngăn cách `;`), `android:name` của `permission` /
   `uses-permission` / `uses-permission-sdk-23` / `action`, `android:taskAffinity`,
   `android:host` và `android:scheme` của `data` (deep link), `android:permission`.
4. **`<queries><package android:name="…">`** chỉ đổi khi giá trị **bằng đúng** package
   cũ (tự tham chiếu). Không thì để nguyên — đó là package của app khác.
5. **Tên class đầy đủ** (`com.example.oldpkg.MainActivity`) **để nguyên** ở chế độ mặc
   định, vì class không di chuyển. Chỉ đổi khi bật `--rewrite-smali`.
6. **Không đổi** `android:resource` và `android:value` của `meta-data` — đây là tham
   chiếu tài nguyên và giá trị cấu hình (Firebase, Maps).
7. Giữ nguyên `${applicationId}` và mọi placeholder khác.

Cài đặt: `ElementTree`, có `register_namespace` cho **toàn bộ** `xmlns:*` khai báo ở
thẻ root.

**Đính chính sau khi thử:** spec bản trước viết "thiếu bước này apktool build lỗi" —
**sai, đã đo lại**. Khi `register_namespace` không có tác dụng, ET xuất prefix `ns0:`
thay cho `android:`; APK vẫn build và vẫn chạy đúng, vì XML namespace được nhận diện
theo **URI** chứ không theo prefix, và aapt2 chấp nhận. Bước đăng ký namespace vì thế
là để giữ manifest dễ đọc và tránh rủi ro với công cụ nhạy cảm với prefix, **không
phải** điều kiện để build được.

Cạm bẫy đã vấp: quét `xmlns:*` phải bỏ qua khai báo `<?xml …?>`, tức tìm `>` của **thẻ
root** chứ không phải `>` đầu tiên trong file. Cần một test khẳng định manifest sau khi
sửa vẫn dùng prefix `android:`.

Nếu manifest có comment thì in cảnh báo rằng comment sẽ bị mất — ET bỏ comment khi
tuần tự hoá lại.

### 5.2 chế độ mặc định — không đụng smali

Có cơ sở thực nghiệm: class `R` vẫn nằm ở package cũ trong dex, nên tham chiếu
`L<package-cũ>/R$…` vẫn resolve. Không sửa gì trong smali.

`references.py` vẫn **quét và báo cáo**: số tham chiếu `L<package-cũ>/`, trong đó bao
nhiêu là `R$`, có thư mục `smali*/<package-cũ>/` không, có `lib/**/*.so` không.

### 5.3 `--rewrite-smali` (tùy chọn)

Chỉ khi được bật: di chuyển `smali*/<cũ>/…` → `smali*/<mới>/…`, thay mọi type
descriptor `L<package-cũ>/` → `L<package-mới>/` với **khớp theo ranh giới** (không để
`oldpkg` khớp vào `oldpkgx`), rồi đổi tên class đầy đủ trong manifest cho khớp.

**Chặn cứng**: nếu bất kỳ `lib/**/*.so` nào chứa symbol `Java_<package-cũ_underscore>_`,
từ chối chạy và in giải thích. Đã đo được `UnsatisfiedLinkError` trong tình huống này.
`--force` để bỏ qua, kèm cảnh báo rằng app sẽ crash khi gọi native.

Bỏ chế độ chỉ thay `L<old>/R$`: đo được không mang lại lợi ích so với mặc định, mà lại
dễ sửa nhầm dòng khai báo `.class`.

### 5.4 `res/` — mặc định chỉ báo cáo

`--rewrite-res` mới sửa. Lý do: `res/values/strings.xml` chứa `google_app_id`,
`gcm_defaultSenderId`, `default_web_client_id` — đổi là hỏng Firebase. Fixture có sẵn
các khoá này để test khẳng định chúng không bị đụng.

## 6. Phát hiện và cảnh báo

| Mục | Hành vi |
|---|---|
| `android:sharedUserId` | **Cảnh báo mạnh**: hai app khác package không chia sẻ được `sharedUserId`; dữ liệu cũ không truy cập được. Vẫn cho chạy. |
| `targetPackage` trong `res/xml/`, `res/xml/*` (shortcuts, appwidget) | quét và báo cáo; sửa khi `--rewrite-res` |
| `<queries>` khai báo package cũ | báo cáo; chỉ sửa nếu đúng bằng package cũ |
| Literal chuỗi chứa package cũ trong smali (`const-string`) | đếm và báo cáo; sửa khi `--rewrite-strings`. Đây là nguyên nhân của ca B2 đã đo: app tự tra cứu theo literal cũ sẽ hỏng. |
| Từ khoá Java trong đoạn package (`com.new.app`) | **Cảnh báo, không chặn** — hợp lệ với `applicationId` nhưng không dùng được làm namespace Java |
| `lib/**/*.so` chứa `Java_<package-cũ>_` | chặn cứng khi bật `--rewrite-smali` (mục 5.3) |
| Package cũ không xuất hiện ở đâu trong manifest lẫn smali | in cảnh báo và dừng (exit 3), trừ khi có `--yes` thì tiếp tục |

## 7. Xử lý lỗi

Exit code: `0` OK · `2` thiếu công cụ · `3` tham số/đầu vào sai · `4` decode lỗi ·
`5` build lỗi · `6` ký lỗi · `7` verify lỗi.

Mỗi bước in `[3/7] Decoding…`. Lỗi thì in output thật của công cụ (không tóm tắt),
giữ lại `work/` và in đường dẫn để soi. Thành công thì xoá `work/` trừ khi
`--keep-work`.

## 8. Kiểm thử

**Unit test** (`unittest`, không cần APK, không cần thiết bị):

- `packagename`: bảng ca hợp lệ/không hợp lệ — một đoạn, `1abc`, `a b`, `a-b`, rỗng,
  đoạn bắt đầu bằng số, từ khoá Java.
- `manifest`: từ chuỗi manifest mẫu, khẳng định mở rộng tên tương đối; đổi `package`;
  đổi authorities/permission/taskAffinity/action/host; **không** đổi
  `google_app_id`, `android:resource`, tên class đầy đủ (ở chế độ mặc định).
- `references`: phát hiện namespace có/không; ranh giới `oldpkg` vs `oldpkgx`; đếm
  đúng số tham chiếu; phát hiện symbol JNI trong `.so`.
- `toolchain`: dò build-tools trên cây thư mục giả trong tmpdir; chọn đúng bản cao
  nhất (`36.0.0` > `9.0.0`).

**End-to-end trên fixture** (`tests/build_fixture.py`, không cần Gradle):
`aapt2 compile` → `aapt2 link --java` (sinh `R.java`) → `javac` → `d8` → đóng gói
`classes.dex` → `zipalign` → `apksigner`. Hai biến thể: `fixture.apk` và
`fixture-jni.apk` (có `lib/arm64-v8a/libfixture.so` do `clang
--target=aarch64-linux-android24` cross-compile).

Fixture phủ: `setContentView(R.layout.x)`, `getIdentifier(…, getPackageName())`,
`getIdentifier(…, "<package cũ>")`, ContentProvider có authorities theo package, custom
permission, taskAffinity, deep link, một activity khai báo tương đối, một activity
khai báo đầy đủ, một native method, và các khoá kiểu Firebase trong `strings.xml`.

Kịch bản: build fixture → chạy công cụ → `adb install` → mở app → đọc logcat, khẳng
định `FIXTURE-OK` và từng case. Chạy cho cả hai biến thể, có và không `--rewrite-smali`.
Đây là bước duy nhất chứng minh được cả pipeline chạy thật.

`tests/measure_modes.py` giữ lại làm harness so sánh chế độ, phục vụ việc đo sau này.

## 9. Sản phẩm giao

```
apkrepack-rename/
├── apkrename/              # package, 7 module
├── tests/
│   ├── fixture/            # nguồn fixture (manifest, java, res, jni)
│   ├── build_fixture.py    # dựng fixture không cần Gradle
│   ├── measure_modes.py    # harness đo chế độ
│   └── test_*.py           # unit test
├── docs/
│   ├── superpowers/specs/  # tài liệu này
│   └── experiments/        # kết quả đo
├── input/                  # (gitignore) bỏ APK vào đây
├── output/                 # (gitignore) APK kết quả
├── keys/                   # (gitignore) debug.keystore tự sinh
├── .tools/                 # (gitignore) apktool jar tải về
├── README.md               # tiếng Việt: yêu cầu, cách dùng, ví dụ, giới hạn
├── KNOWN_ISSUES.md         # danh sách ca app có thể lỗi sau khi đổi tên
└── .gitignore
```

`KNOWN_ISSUES.md` phủ: Firebase/FCM (`google-services.json` ràng buộc package, token
cũ không dùng được), Google Maps API key (ràng buộc package + SHA-1, phải thêm key mới
trên console), Google Sign-In (ràng buộc package + SHA-1), chữ ký đổi nên không update
đè được app cũ và dữ liệu cũ bị cô lập, Play Integrity / SafetyNet (đòi chữ ký và
package đã đăng ký), deep link đã đăng ký (app khác vẫn giữ), app tự tra cứu theo
literal package cũ (ca B2 đã đo), `sharedUserId`, và tài khoản/đăng nhập gắn với
package.

`.gitignore`: `input/`, `output/`, `work/`, `keys/`, `.tools/`, `*.jks`, `*.keystore`,
`*.apk`, `tests/build/`, `tests/work/`, `tests/out/`, `__pycache__/`.

## 10. Rủi ro và điều chưa đo

- Fixture là app đơn giản, code namespace trùng package. Chưa đo app dùng R8 full mode
  (R class bị inline) hay resource shrinking; các trường hợp này dự kiến còn dễ hơn vì
  không còn tham chiếu R dạng class.
- Chưa đo app có namespace khác package.
- Chưa đo split APK, đã loại khỏi phạm vi.
- Chưa đo trên Windows; nhánh Windows viết theo tài liệu, chưa chạy thật.
- `apktool` 3.x là bản duy nhất được test; script cảnh báo nếu gặp bản khác.
