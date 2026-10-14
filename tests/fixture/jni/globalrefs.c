/*
 * Native library for the JNI hazard case.
 *
 * Compiled without jni.h on purpose: we only need the exported symbol name to
 * look like a JNI entry point, so the rename tool's JNI detection has a real
 * target to find. Returning 0 (NULL) stands in for a jstring.
 */
const char *Java_com_example_oldpkg_MainActivity_nativeHello(void *env, void *clazz) {
    (void) env;
    (void) clazz;
    return 0;
}
