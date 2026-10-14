package com.example.oldpkg;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;

/** Declared as ".RelativeActivity" in the manifest to test decode normalisation. */
public class RelativeActivity extends Activity {
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(R.layout.activity_main);
        Log.i(TAG, "RelativeActivity-OK");
    }

    private static final String TAG = "RenameFixture";
}
