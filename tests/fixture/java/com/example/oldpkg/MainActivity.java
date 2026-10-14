package com.example.oldpkg;

import android.app.Activity;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;

/**
 * Fixture activity exercising every rename hazard we care about.
 * Each case logs a distinct tag so `adb logcat` alone says which one broke.
 */
public class MainActivity extends Activity {
    private static final String TAG = "RenameFixture";

    private static native String nativeHello();

    static {
        try {
            System.loadLibrary("fixture");
            Log.i(TAG, "JNI-LIB-LOADED");
        } catch (Throwable t) {
            Log.i(TAG, "JNI-LIB-ABSENT: " + t);
        }
    }

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        Log.i(TAG, "=== MainActivity.onCreate package=" + getPackageName() + " ===");

        // case A: R class reference baked into this class's constant pool
        setContentView(R.layout.activity_main);
        Log.i(TAG, "caseA-OK layout=" + R.layout.activity_main
                + " root=" + R.id.root
                + " label=" + R.id.label
                + " appName=" + R.string.app_name);

        // case B: runtime resource lookup keyed by the RUNTIME package name
        String pkg = getPackageName();
        int byRuntime = getResources().getIdentifier("activity_main", "layout", pkg);
        Log.i(TAG, "caseB getIdentifier('activity_main','layout','" + pkg + "') = " + byRuntime);
        if (byRuntime == 0) {
            Log.e(TAG, "caseB-FAILED runtime-package resource lookup returned 0");
        }

        // case B2: runtime lookup keyed by the ORIGINAL hardcoded package name
        int byLiteral = getResources().getIdentifier("activity_main", "layout", "com.example.oldpkg");
        Log.i(TAG, "caseB2 getIdentifier(...,'com.example.oldpkg') = " + byLiteral);
        if (byLiteral == 0) {
            Log.w(TAG, "caseB2-LITERAL returned 0 (expected once the package is renamed)");
        }

        // case C: provider authority is derived from the package name
        String auth = pkg + ".fileprovider";
        try {
            Uri u = Uri.parse("content://" + auth + "/files/x.txt");
            String type = getContentResolver().getType(u);
            Log.i(TAG, "caseC-OK authority=" + auth + " type=" + type);
        } catch (Throwable t) {
            Log.e(TAG, "caseC-FAILED authority=" + auth, t);
        }

        // case D: native method, resolved by the JNI symbol name
        try {
            Log.i(TAG, "caseD nativeHello() = " + nativeHello());
        } catch (Throwable t) {
            Log.i(TAG, "caseD-SKIPPED " + t);
        }

        Log.i(TAG, "FIXTURE-OK");
    }
}
