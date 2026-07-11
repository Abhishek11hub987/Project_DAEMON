/*
 * sysinfo.c - Minimal C smoke-test for D.A.E.M.O.N. C-integration skill.
 *
 * Prints OS, architecture, pointer size, and a heartbeat message.
 * Use this to verify the gcc/clang toolchain + Python wrapper before
 * exercising heavier programs.
 *
 * Build:  gcc -O2 -o sysinfo sysinfo.c
 * Run:    ./sysinfo
 */

#include <stdio.h>
#include <time.h>

int main(int argc, char **argv) {
#if defined(_WIN32) || defined(_WIN64)
    const char *os = "Windows";
#elif defined(__linux__)
    const char *os = "Linux";
#elif defined(__APPLE__)
    const char *os = "macOS";
#else
    const char *os = "Unknown";
#endif

#if defined(__x86_64__) || defined(_M_X64)
    const char *arch = "x86_64";
#elif defined(__aarch64__) || defined(_M_ARM64)
    const char *arch = "arm64";
#elif defined(__i386__) || defined(_M_IX86)
    const char *arch = "x86";
#else
    const char *arch = "unknown";
#endif

    time_t now = time(NULL);
    struct tm *t = localtime(&now);

    printf("DAEMON C-module heartbeat\n");
    printf("OS         : %s\n", os);
    printf("Arch       : %s\n", arch);
    printf("Ptr size   : %zu bytes\n", sizeof(void *));
    printf("Local time : %04d-%02d-%02d %02d:%02d:%02d\n",
           t->tm_year + 1900, t->tm_mon + 1, t->tm_mday,
           t->tm_hour, t->tm_min, t->tm_sec);
    printf("Args       : %d\n", argc);
    for (int i = 0; i < argc; ++i) {
        printf("  argv[%d] = %s\n", i, argv[i]);
    }
    return 0;
}
