# Đo 4 chế độ đổi package name — kết quả thực nghiệm

Ngày: 2026-10-14
Thiết bị: 25019PNF3C, Android 16, arm64-v8a, build `user`
Fixture: `tests/fixture/` → `tests/out/fixture.apk`, `tests/out/fixture-jni.apk`
Đổi `com.example.oldpkg` → `com.example.newpkg`
Harness: `tests/measure_modes.py`

## Fixture kiểm gì

| Case | Nội dung | Log |
|---|---|---|
| A | `setContentView(R.layout.activity_main)` — tham chiếu R class nằm trong constant pool của smali | `caseA-OK` |
| B | `getResources().getIdentifier("activity_main","layout", getPackageName())` | `caseB` |
| B2 | `getIdentifier(..., "com.example.oldpkg")` — tra cứu bằng literal package cũ | `caseB2` |
| C | `ContentProvider` với `android:authorities="com.example.oldpkg.fileprovider"` | `caseC-OK type=…` |
| D | `native String nativeHello()` ↔ symbol `Java_com_example_oldpkg_MainActivity_nativeHello` trong `lib/arm64-v8a/libfixture.so` | `caseD` |

Baseline cả hai biến thể đều `FIXTURE-OK`; biến thể JNI có `JNI-LIB-LOADED` và `caseD nativeHello() = null`.

## Kết quả

| Chế độ | apktool | A (R class) | B (runtime pkg) | C (authority) | D (JNI) | Kết luận |
|---|---|---|---|---|---|---|
| (a) chỉ sửa manifest | 3.0.3 | OK | OK | OK | OK¹ | **PASS** |
| (b) manifest + thay `L<old>/R$` | 3.0.3 | OK | OK | OK | — | **PASS** |
| (c) manifest + di chuyển namespace | 3.0.3 | OK | OK | OK | **HỎNG** | **PARTIAL** |
| (d) `renameManifestPackage` | 2.12.1 + `--use-aapt1` | OK | **HỎNG** | **HỎNG âm thầm** | — | **PARTIAL** |

¹ (a) chạy trên `fixture-jni.apk`: `JNI-LIB-LOADED` + `caseD nativeHello() = null` → JNI nguyên vẹn.

## Ba kết luận quan trọng

### 1. apktool KHÔNG sinh lại class R ở package mới

Đây là điểm tôi đã đoán sai và cần ghi lại rõ. Đếm class trong `classes.dex` của APK đã build:

| Chế độ | class ở `oldpkg` | class ở `newpkg` | R class ở `old` | R class ở `new` |
|---|---|---|---|---|
| (a) | 10 | 0 | 5 | 0 |
| (b) | 6 | 4 | 1 | 4 |
| (c) | 0 | 10 | 0 | 5 |
| (d) | 10 | 0 | 5 | 0 |

Ở chế độ (a), **toàn bộ class — kể cả 5 class `R$*` — vẫn nằm ở package cũ**. Nghĩa là class `R` mà apktool decode được từ APK gốc vẫn được biên dịch nguyên vẹn vào dex. Vì thế tham chiếu `Lcom/example/oldpkg/R$layout;` trong smali vẫn resolve, và case A chạy.

**Giả định ban đầu của tôi — "đổi `package` sẽ khiến aapt2 sinh lại R ở package mới nên smali trỏ vào class không tồn tại" — là SAI.** Không cần chế độ (b) để cứu việc này.

Ở chế độ (b), 4 class `R$*` xuất hiện ở package mới là do regex của harness khớp cả dòng khai báo `.class public final Lcom/example/oldpkg/R$layout;`, tức nó đã **đổi tên định nghĩa class** chứ không chỉ đổi tham chiếu. Đây là tác dụng phụ của cách cài đặt, không phải apktool sinh lại R.

### 2. Chế độ (d) tệ hơn hẳn, và hỏng âm thầm

`--rename-manifest-package` **không còn là CLI option** ở bất kỳ bản apktool nào cài được hôm nay. Chuỗi này chỉ còn trong `brut/androlib/AaptInvoker.class` — nó là cờ của **aapt1**. Đường đi thật là khoá `packageInfo.renameManifestPackage` trong `apktool.yml` + `--use-aapt1`.

Khi chạy được thì nó hỏng hai chỗ:

- `caseB` → `getIdentifier(...,'com.example.newpkg') = 0` trong khi `getIdentifier(...,'com.example.oldpkg')` vẫn trả về ID. Tức aapt1 đổi thuộc tính `package` trong manifest nhưng **không đổi package trong bảng tài nguyên** (`resources.arsc`).
- `caseC` → `type=null`. Resolver không tìm thấy provider, nhưng `getType()` **không ném exception**, nên nếu chỉ nhìn "app có crash không" thì tưởng là chạy được. Đây là lý do harness phải assert trên giá trị `type` trả về chứ không chỉ bắt exception.

Ngược lại ở (a)/(b)/(c), vì manifest `package` được sửa *trước* khi build nên aapt2 link lại bảng tài nguyên theo package mới → `caseB` chạy, `caseB2` trả 0.

### 3. Rủi ro JNI là thật, đã đo được

- (a) trên fixture JNI: lib load bình thường, `caseD nativeHello() = null` → **an toàn**.
- (c) trên fixture JNI: lib vẫn load nhưng JNI tìm `Java_com_example_newpkg_MainActivity_nativeHello`, trong khi `.so` chỉ export `Java_com_example_oldpkg_MainActivity_nativeHello` → `UnsatisfiedLinkError`.

Suy ra: mọi chế độ **di chuyển namespace smali** đều phải bị chặn cứng khi APK có `lib/**/*.so` chứa symbol `Java_<package-cũ>_`. Chỉ sửa manifest thì không đụng tới JNI.

## Khuyến nghị chốt mặc định

**Mặc định = (a) chỉ sửa manifest.** Có bằng chứng thực nghiệm cho cả APK thường lẫn APK có native lib, vùng ảnh hưởng nhỏ nhất, và không cần đụng smali.

- `--rewrite-smali` (chế độ c) để riêng, chỉ dùng khi test cho thấy cần, và **chặn cứng khi phát hiện symbol JNI**.
- Bỏ chế độ (b): không mang lại lợi ích gì so với (a) trên fixture này, mà lại có rủi ro sửa nhầm dòng khai báo class.
- Không dùng chế độ (d): không khả dụng trên apktool hiện hành, và khi chạy được thì để lại bảng tài nguyên chưa đổi.

## Điều chưa đo được, cần lưu ý

- Fixture là app **đơn giản**, code namespace trùng package. App thật có thể khác.
- Chưa đo app dùng R8 full mode (R class bị inline) hay resource shrinking.
- App tự tra cứu resource bằng literal package cũ (case B2) sẽ hỏng với **mọi** chế độ — đây là hạn chế cố hữu của việc đổi tên, không phải lỗi chế độ. Cần ghi vào KNOWN_ISSUES.
- `lib/arm64-v8a/libfixture.so` được cross-compile bằng `clang --target=aarch64-linux-android24` (nguồn C không cần header nên không cần NDK).
