package com.example.oldpkg;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.util.Log;

/**
 * Stand-in for androidx FileProvider. The rename-relevant part is the
 * android:authorities string in the manifest, which is identical either way;
 * this avoids pulling an AAR into the fixture.
 */
public class FixtureProvider extends ContentProvider {
    private static final String TAG = "RenameFixture";

    @Override
    public boolean onCreate() {
        Log.i(TAG, "provider-onCreate");
        return true;
    }

    @Override
    public String getType(Uri uri) {
        return "application/octet-stream";
    }

    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public Uri insert(Uri u, ContentValues v) { return null; }
    @Override public int delete(Uri u, String s, String[] a) { return 0; }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { return 0; }
}
