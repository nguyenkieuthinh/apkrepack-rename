# apkrename

Đổi package name của một file APK (`com.ten.app` → `com.ten.app2`) sao cho APK
sau khi đổi vẫn cài và chạy được.

> **Chỉ dùng cho APK của chính bạn hoặc APK bạn được phép chỉnh sửa.**

## Cách dùng

```bash
python3 apkrename.py duongdan/file.apk
python3 apkrename.py duongdan/file.apk com.ten.app2   # khỏi nhập tay
python3 apkrename.py                                  # hỏi đường dẫn APK
python3 apkrename.py --help
```

Chạy không tham số thì script hỏi `Nhập đường dẫn APK: `, rồi tới bước 5 hỏi
`Nhập package mới: `.

| Tùy chọn | Tác dụng |
|---|---|
| `--no-restore-res` | Gặp APK có package tài nguyên phụ thì **dừng**, không tự dựng lại (xem mục "APK có package tài nguyên phụ"). Mặc định là tự dựng lại. |

Kết quả nằm ở `output/<tên file gốc>_<package mới>.apk`, kèm lệnh `adb install`.

## Yêu cầu

| Công cụ | Ghi chú |
|---|---|
| Python 3.9+ | chỉ dùng thư viện chuẩn |
| JDK 17+ | để chạy apktool |
| apktool | trên `PATH`, hoặc đặt `apktool_*.jar` vào `.tools/` |
| zipalign, apksigner | trong Android SDK build-tools, cần `ANDROID_HOME` hoặc `~/Android/Sdk` |
| aapt2 | trong cùng build-tools; chỉ cần khi APK có package tài nguyên phụ |
| keytool | có sẵn trong JDK |

Thiếu công cụ nào script báo `[THIẾU]` kèm lệnh cài rồi dừng, không chạy tiếp.

Keystore debug tự tạo ở `~/.android/debug.keystore` nếu chưa có (alias
`androiddebugkey`, mật khẩu `android`). Không có keystore nào bị commit.

## Luồng chạy

1. Kiểm tra công cụ.
2. Kiểm tra file APK tồn tại và hợp lệ.
3. `apktool d` → tìm package hiện tại.
4. Kiểm tra có đổi được an toàn không (JNI, package tài nguyên phụ — nếu có thì
   dựng lại ngay tại đây, trước khi hỏi package mới, để lỗi không tới sau vài
   phút build).
5. Hỏi package mới và kiểm tra hợp lệ.
6. Sửa manifest → `apktool b` → `zipalign` → `apksigner sign`.
7. In đường dẫn APK kết quả và lệnh `adb install`.

## Chỉ sửa manifest

Script **chỉ sửa `AndroidManifest.xml`** (cộng thêm đúng một file mới
`res/values/apkrename_restored.xml` khi APK có package tài nguyên phụ), không
đụng smali, không sửa tài nguyên sẵn có, không đụng `assets/`. Đây là kết quả
đo, không phải phỏng đoán — xem `docs/experiments/2026-10-14-rename-modes.md`.

Cụ thể:

- Thuộc tính `package` của `<manifest>` được đổi.
- Tên class viết tắt (`.MainActivity`) được chuyển thành tên đầy đủ theo package
  **cũ** *trước khi* đổi package — nếu không, aapt2 sẽ hiểu `.MainActivity` là
  `com.ten.app2.MainActivity` trong khi smali vẫn nằm ở `com/ten/app/`.
- Không sửa `android:resource` / `android:value` → `google_app_id` của Firebase
  và các giá trị cấu hình khác giữ nguyên.
- Không sửa tham chiếu tài nguyên nội bộ (`@style/x`, `?attr/x`) — riêng tham
  chiếu trỏ sang **package tài nguyên khác** thì được dựng lại và trỏ sang tên
  mới, xem mục "APK có package tài nguyên phụ".
- `android:authorities` của mọi `<provider>` được đảm bảo chứa package mới —
  xem mục "Cài song song với bản gốc".
- So khớp package theo ranh giới nên `cct.com.ten.app` (app khác) không bị sửa nhầm.

Vì chỉ sửa manifest, class `R` vẫn nằm ở package cũ trong dex — tham chiếu
`Lcom/ten/app/R$layout;` trong smali vẫn resolve bình thường.

## APK có package tài nguyên phụ (bản tải từ Google Play)

Bản tải từ Play có `resources.arsc` chứa **nhiều package tài nguyên** — một cho
app chính, mỗi dynamic feature module thêm một package. apktool chỉ giải mã được
package chính và bỏ rơi tài nguyên của các package phụ, nhưng manifest vẫn còn
tham chiếu vào chúng. Để nguyên thì lúc build, `aapt2 link --package-id 127`
không thấy các package đó nên báo:

```
error: resource com.ten.app.df_kakao:style/a not found
error: failed processing manifest
```

Đo trên TikTok 43.8.3: 3 package tài nguyên (`0x7f` app + `0x6c` df_kakao
+ `0x6d` df_line), apktool giải mã 71.915/71.921 tài nguyên. **Giải mã rồi build
lại không sửa một byte nào vẫn hỏng y hệt** — đây là giới hạn của apktool với
loại APK này, không phải lỗi của việc đổi tên.

**Script tự xử lý.** Ngay ở bước 4 (trước khi hỏi package mới), script:

1. quét manifest tìm các giá trị dạng `@<package tài nguyên khác>:<loại>/<tên>`;
2. đọc bảng tài nguyên gốc bằng `aapt2 dump resources` (đây là chỗ duy nhất
   script cần tới `aapt2`);
3. dựng lại từng tài nguyên đó thành tài nguyên **của package chính**, tên mới
   (ví dụ `df_kakao:style/a` → `style/df_kakao_a`), ghi vào
   `res/values/apkrename_restored.xml`; tài nguyên mà nó trỏ tới cũng được dựng
   theo, kể cả tài nguyên của package phụ khác;
4. trỏ manifest sang tên mới.

Chỉ dựng lại được tài nguyên dạng **style** — cũng là thứ manifest hay trỏ tới
nhất (`android:theme`). Gặp dạng khác (layout, string, drawable...) script báo
lỗi rõ ràng và dừng, không đoán.

Muốn dừng thay vì dựng lại, dùng `--no-restore-res`.

## Cài song song với bản gốc

Đổi package **không** đủ để cài song song bản gốc: Android còn bắt buộc
`android:authorities` của `ContentProvider` **duy nhất trên toàn máy**, không
phải duy nhất trong app. Một authority là hằng số của SDK, không chứa package,
thì bản gốc và bản đổi tên dùng chung đúng chuỗi đó và cài đặt đổ ngay:

```
INSTALL_FAILED_CONFLICTING_PROVIDER: Scanning Failed.: Can't install because
provider name com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233
(in package com.zhiliaoapp.mus2) is already used by
com.zhiliaoapp.musically
```

Đo trên TikTok 43.8.3: 23 authority, **21 cái chứa package** (đổi theo package là
xong), **2 cái không**:

| Authority | Nguồn |
|---|---|
| `com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233` | SDK nội bộ ByteDance |
| `com.facebook.app.FacebookContentProvider597615686992125` | Facebook SDK, `<app-id>` của TikTok |

Script nối package mới vào đuôi hai cái đó:

```
com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233
  -> com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233.com.zhiliaoapp.mus2
```

**Đánh đổi:** SDK nào tra authority bằng chuỗi cứng (Facebook SDK tính
`"com.facebook.app.FacebookContentProvider" + appId` rồi `resolveContentProvider`)
sẽ không tìm thấy nữa → tính năng đó hỏng. Nhưng giữ nguyên thì APK **không cài
được chút nào**, nên đây là lựa chọn bắt buộc, không phải tùy chọn. Việc đổi
được in rõ ở bước 6.

Quyền tự khai báo (`<permission android:name>`) cũng phải duy nhất trên toàn máy,
nếu không sẽ là `INSTALL_FAILED_DUPLICATE_PERMISSION`. Script **không tự sửa**
loại này — app khác có thể đang xin đúng tên quyền đó — mà **cảnh báo** nếu gặp.
TikTok 43.8.3 có 5 quyền khai báo, cả 5 đều chứa package nên không dính.

## APK không hỗ trợ

Script **dừng ngay** khi gặp các loại sau, không build.

### 1. Split APK / XAPK

APK tách thành nhiều file (`.apks`, `.xapk`, `base.apk` + `split_config.*.apk`)
không đổi tên được bằng cách này — tài nguyên và code nằm rải ở các file, phải
đổi đồng bộ và ký lại cả bộ.

### 2. App có thư viện native gắn với package

Nếu `lib/**/*.so` export hàm dạng `Java_<package_cũ>_<Class>_<method>` (JNI đăng
ký tĩnh), đổi package làm hàm native không tìm thấy class → `UnsatisfiedLinkError`.
Script quét `.so` và chặn cứng.

App dùng `JNI_OnLoad` + `RegisterNatives` (đăng ký động) thì không bị ảnh hưởng.

### 3. App kiểm tra chữ ký hoặc Play Integrity

Script ký bằng keystore debug, nên chữ ký **khác hoàn toàn** bản gốc. App nào
kiểm tra chữ ký của chính nó, hoặc xác thực qua Play Integrity / SafetyNet, sẽ
từ chối chạy. Không có cách sửa bằng script.

## Sau khi đổi tên, app có thể vẫn hỏng

Đổi được package **không** bảo đảm app chạy. Các trường hợp thường gặp:

| Nguyên nhân | Biểu hiện |
|---|---|
| Server buộc package với chữ ký gốc | Đăng nhập / API trả lỗi |
| Kiểm tra chữ ký, Play Integrity | Thoát ngay khi mở, hoặc chặn tính năng |
| App tự tra resource bằng literal package cũ (`getIdentifier(name, type, "com.ten.app")`) | Thiếu giao diện, sai màn hình |
| Google Maps API key giới hạn theo package + SHA-1 | Bản đồ trắng |
| Firebase `google_app_id` gắn với package trên console | Push, Analytics, Auth không chạy |
| Deep link / App Links đăng ký theo package | Link không mở app |
| Google Sign-In, thanh toán in-app | Lỗi xác thực |
| SDK tra `ContentProvider` bằng chuỗi authority cứng (Facebook share, một số SDK ByteDance) | Tính năng đó không tìm thấy provider |

Ngoài ra APK kết quả được ký bằng keystore debug nên **không cập nhật đè** lên
bản đang cài (khác chữ ký). Giữ nguyên package cũ thì phải gỡ bản cũ trước; đổi
sang package mới thì cài song song được.

## Cây thư mục

```
apkrepack-rename/
├── apkrename.py              công cụ, một file, chỉ dùng thư viện chuẩn
├── README.md
├── input/                    APK đầu vào (không commit)
├── output/                   APK kết quả (không commit)
├── work/                     thư mục tạm khi chạy (không commit)
├── .tools/                   apktool_*.jar nếu không cài vào PATH (không commit)
├── docs/
│   ├── experiments/          số liệu đo (4 chế độ đổi tên, xử lý dynamic feature)
│   └── superpowers/specs/    bản thiết kế ban đầu
└── tests/
    ├── fixture/              app mẫu: Activity, FileProvider, JNI
    ├── build_fixture.py      dựng fixture không cần Gradle
    ├── measure_modes.py      harness đo, không phải công cụ chính
    └── out/                  fixture.apk, fixture-jni.apk (không commit)
```

## Kiểm thử

```bash
python3 tests/build_fixture.py            # dựng lại fixture
python3 apkrename.py tests/out/fixture.apk com.example.newpkg
```

Fixture có 4 ca: `setContentView(R.layout.x)`, `getIdentifier()` tra theo
`getPackageName()`, `FileProvider` với `android:authorities`, và một biến thể có
`lib/arm64-v8a/libfixture.so` export symbol JNI để kiểm tra nhánh chặn.

Ca dynamic feature đo trên TikTok 43.8.3, số liệu ở
`docs/experiments/2026-10-14-dynamic-feature-resources.md`.
