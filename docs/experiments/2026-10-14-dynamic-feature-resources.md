# APK có package tài nguyên phụ (dynamic feature) — đo và cách xử lý

Ngày: 2026-10-14
APK đo: `input/TikTok_43.8.3.apk` (243.443.751 bytes, `com.zhiliaoapp.musically`, 21.973 file, 50 dex)
Công cụ: apktool 3.0.3, aapt2 build-tools 36.0.0, JDK 27

## 1. Triệu chứng

Sau `apktool d` rồi `apktool b` **không sửa gì**, build đổ:

```
error: resource com.zhiliaoapp.musically.df_kakao:style/a not found.
error: failed processing manifest.
```

## 2. Nguyên nhân gốc — đã chứng minh, không phải phỏng đoán

`aapt2 dump resources` trên APK gốc cho thấy `resources.arsc` chứa **3 package tài nguyên**:

| Package | id | Số tài nguyên |
|---|---|---|
| `com.zhiliaoapp.musically` | `0x7f` | 71.915 |
| `com.zhiliaoapp.musically.df_kakao` | `0x6c` | 3 |
| `com.zhiliaoapp.musically.df_line` | `0x6d` | 3 |

apktool gộp tất cả vào một cây `res/` và một `public.xml` (**71.915 entry, tất cả ở `0x7f`**) — 6 tài nguyên của hai package phụ bị bỏ rơi, nhưng manifest vẫn còn 2 tham chiếu tới chúng:

```
activity/theme = @com.zhiliaoapp.musically.df_kakao:style/a
activity/theme = @com.zhiliaoapp.musically.df_line:style/aj7
```

Lúc build apktool chạy `aapt2 link ... --package-id 127`, chỉ có package `0x7f` tồn tại → tham chiếu không resolve được.

**Đây là giới hạn của apktool, không phải lỗi của việc đổi tên**: decode rồi build lại với **zero thay đổi** hỏng y hệt. Manifest nhị phân gốc lưu hai tham chiếu này dưới dạng id thô (`@0x6c030000`, `@0x6d020001`); apktool *ký hiệu hoá* chúng lúc decode (0 lần xuất hiện chuỗi `df_kakao` trong manifest nhị phân gốc), rồi không giải mã được package đích.

## 3. Cách xử lý: dựng lại tài nguyên vào package chính

Ở bước 4 (trước khi hỏi package mới), script:

1. quét manifest tìm `@<pkg>:<type>/<name>` với `<pkg>` khác package app;
2. `aapt2 dump resources` trên APK gốc → bảng `{id: {pkg, type, name, kind, parent, items}}`;
3. sinh lại từng tài nguyên được trỏ tới thành tài nguyên **của package chính**, tên mới đặt theo `df_kakao_a` (tiền tố = đoạn cuối của package phụ, có kiểm tra đụng tên);
4. ghi vào `res/values/apkrename_restored.xml`, rồi trỏ manifest sang tên mới.

Tài nguyên mà nó trỏ tới cũng được dựng theo, kể cả của package phụ khác. Attr framework (`0x0101xxxx`) tra tên qua `aapt2 dump resources` của framework apktool (`~/.local/share/apktool/framework/1.apk`) hoặc `android.jar` trong SDK.

Chỉ dựng lại được **style** — cũng là thứ manifest hay trỏ tới nhất (`android:theme`). Gặp layout/string/drawable... thì báo lỗi rõ ràng và dừng, không đoán.

### Kết quả sinh ra

```xml
<style name="df_kakao_a" parent="@style/rm">
    <item name="android:windowBackground">@android:color/transparent</item>
    <item name="android:windowNoTitle">true</item>
    ...
</style>
<style name="df_line_aj6">
    <item name="android:activityOpenEnterAnimation">@android:anim/fade_in</item>
    <item name="android:activityCloseExitAnimation">@android:anim/fade_out</item>
</style>
<style name="df_line_aj7">
    ...
    <item name="android:windowAnimationStyle">@style/df_line_aj6</item>
</style>
```

`df_line_aj7` trỏ tới `df_line:style/aj6` → tự kéo theo, và tham chiếu được trỏ sang tên mới.

## 4. Lỗi trong chính script, tìm bằng probe riêng trước khi build

`FrameworkNames.get(0x01010054)` trả `None`, dù `aapt2 dump resources 1.apk` **có** dòng đó:

```
resource 0x01010054 attr/windowBackground PUBLIC
```

Regex cũ `^    resource 0x([0-9a-f]+) (\S+)/(\S+)$` neo `$` ngay sau tên nên **bỏ qua mọi dòng có đuôi ` PUBLIC`** — mất 11.807 trong 11.807+ attr của framework, không tra được tên `android:windowBackground`. Sửa thành `(?: [A-Z]+)?$`.

Hệ quả nếu không phát hiện: `RestoreError: không tra được tên attr 0x1010054` → script dừng, dù lẽ ra chạy được.

## 5. Kiểm chứng trên file APK kết quả

`output/TikTok_43.8.3_com.zhiliaoapp.mus2.apk` (255.823.687 bytes), đo bằng `aapt2`/`apksigner`, không dựa vào log của script:

| Hạng mục | Kết quả |
|---|---|
| `package` | `com.zhiliaoapp.mus2`, versionCode `2024308030`, versionName `43.8.3` giữ nguyên |
| Package tài nguyên trong bảng mới | chỉ còn `com.zhiliaoapp.mus2` id `7f` |
| Tổng tài nguyên | 71.918 = 71.915 gốc + 3 dựng lại |
| Ánh xạ theme | `@0x6c030000` → `@0x7f130006` (`style/df_kakao_a`), `@0x6d020001` → `@0x7f130013` (`style/df_line_aj7`) |
| Còn tham chiếu `df_kakao`/`df_line` nào | 0 |
| dex | 50 → 50 |
| Số entry trong zip | 21.973 → 21.973 |
| Chữ ký | `apksigner verify`: v1 + v2 + v3 đều true, 1 signer, chứng chỉ debug |

3 tài nguyên còn lại của package phụ (2 layout + 1 string) không được dựng lại. Đã đếm tham chiếu từ 50 file dex tới 3 id đó: **0**. Vì vậy chúng không cần thiết cho package chính.

## 6. Cài lên máy: INSTALL_FAILED_CONFLICTING_PROVIDER

Lần cài đầu tiên lên máy thật (25019PNF3C, Android 16) đổ:

```
INSTALL_FAILED_CONFLICTING_PROVIDER: Scanning Failed.: Can't install because
provider name com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233
(in package com.zhiliaoapp.mus2) is already used by com.zhiliaoapp.musically
```

Đếm bằng `aapt2 dump xmltree` trên manifest gốc: **23 authority** trong
`<provider>`; **21 cái chứa `com.zhiliaoapp.musically`** nên được `replace_pkg`
đổi theo, **2 cái là hằng số không chứa package** nên giữ nguyên y hệt bản gốc:

| Authority | 50 dex có chứa chuỗi này? |
|---|---|
| `com.ss.android.common.multiprocess.SHARE_PROVIDER_AUTHORITY1233` | 0 |
| `com.facebook.app.FacebookContentProvider597615686992125` | 0 |

Android bắt buộc authority duy nhất **trên toàn máy**, không phải trong app — nên
đây là lỗi cài đặt, không phải lỗi chạy. Đã sửa: authority nào không chứa package
mới thì nối thêm `.<package mới>` vào đuôi.

Kiểm luôn loại xung đột còn lại: `<permission android:name>` cũng phải duy nhất
toàn máy (`INSTALL_FAILED_DUPLICATE_PERMISSION`). TikTok khai báo 5 quyền, cả 5
đều chứa package → không dính. `android:sharedUserId` và `android:targetPackage`:
không có trong manifest. Nên authority là nguồn xung đột duy nhất.

## 7. Chưa đo được

- Trước 2026-10-14 phần authority chưa được kiểm: APK build xong, ký hợp lệ, nhưng
  `adb install` đổ `INSTALL_FAILED_CONFLICTING_PROVIDER` (mục 6). Mức APK thôi thì
  không phát hiện được lỗi này.
- APK ký bằng keystore debug nên chữ ký khác bản gốc — app kiểm tra chữ ký hoặc Play Integrity vẫn sẽ hỏng, xem README.
- Chỉ dựng lại được style. APK mà manifest trỏ tới layout/string/drawable của package phụ sẽ dừng với lỗi rõ ràng.
