/*
 * file_sorter.c - List files in a directory sorted by size (descending).
 *
 * Usage:
 *   file_sorter [path] [--asc|--desc] [--limit N]
 *
 * Defaults:
 *   path  = "."        current directory
 *   order = --desc     largest first
 *   limit = 50         top-N entries
 *
 * Cross-platform (Windows + POSIX) using stat / _stat64.
 *
 * Build:
 *   gcc -O2 -Wall -o file_sorter file_sorter.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32) || defined(_WIN64)
  #include <windows.h>
  #include <sys/stat.h>
  typedef long long file_size_t;
#else
  #include <dirent.h>
  #include <sys/stat.h>
  #include <unistd.h>
  typedef long long file_size_t;
#endif

#define MAX_ENTRIES 4096
#define MAX_NAME    512

typedef struct {
    char name[MAX_NAME];
    file_size_t size;
} Entry;

static int g_descending = 1;

static int cmp_size(const void *a, const void *b) {
    const Entry *ea = (const Entry *)a;
    const Entry *eb = (const Entry *)b;
    if (ea->size == eb->size) return strcmp(ea->name, eb->name);
    if (g_descending)
        return (eb->size > ea->size) - (eb->size < ea->size);
    return (ea->size > eb->size) - (ea->size < eb->size);
}

static void format_size(file_size_t bytes, char *out, size_t outsz) {
    const char *units[] = {"B", "KB", "MB", "GB", "TB"};
    int u = 0;
    double v = (double)bytes;
    while (v >= 1024.0 && u < 4) { v /= 1024.0; ++u; }
    snprintf(out, outsz, "%8.2f %2s", v, units[u]);
}

#if defined(_WIN32) || defined(_WIN64)
static int collect_entries(const char *path, Entry *entries, int max) {
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof(pattern), "%s\\*", path);

    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "Cannot open directory: %s\n", path);
        return -1;
    }
    int count = 0;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        if (count >= max) break;
        strncpy(entries[count].name, fd.cFileName, MAX_NAME - 1);
        entries[count].name[MAX_NAME - 1] = '\0';
        LARGE_INTEGER sz;
        sz.LowPart = fd.nFileSizeLow;
        sz.HighPart = (LONG)fd.nFileSizeHigh;
        entries[count].size = (file_size_t)sz.QuadPart;
        ++count;
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return count;
}
#else
static int collect_entries(const char *path, Entry *entries, int max) {
    DIR *d = opendir(path);
    if (!d) {
        fprintf(stderr, "Cannot open directory: %s\n", path);
        return -1;
    }
    struct dirent *de;
    int count = 0;
    while ((de = readdir(d)) != NULL && count < max) {
        if (de->d_name[0] == '.' &&
            (de->d_name[1] == '\0' ||
             (de->d_name[1] == '.' && de->d_name[2] == '\0'))) continue;
        char full[1024];
        snprintf(full, sizeof(full), "%s/%s", path, de->d_name);
        struct stat st;
        if (stat(full, &st) != 0) continue;
        if (!S_ISREG(st.st_mode)) continue;
        strncpy(entries[count].name, de->d_name, MAX_NAME - 1);
        entries[count].name[MAX_NAME - 1] = '\0';
        entries[count].size = (file_size_t)st.st_size;
        ++count;
    }
    closedir(d);
    return count;
}
#endif

int main(int argc, char **argv) {
    const char *path = ".";
    int limit = 50;

    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--asc") == 0)        g_descending = 0;
        else if (strcmp(argv[i], "--desc") == 0)  g_descending = 1;
        else if (strcmp(argv[i], "--limit") == 0 && i + 1 < argc)
            limit = atoi(argv[++i]);
        else
            path = argv[i];
    }

    Entry *entries = (Entry *)calloc(MAX_ENTRIES, sizeof(Entry));
    if (!entries) {
        fprintf(stderr, "Out of memory\n");
        return 2;
    }

    int n = collect_entries(path, entries, MAX_ENTRIES);
    if (n < 0) { free(entries); return 1; }

    qsort(entries, n, sizeof(Entry), cmp_size);

    printf("Directory : %s\n", path);
    printf("Files     : %d  (showing top %d, %s)\n",
           n, n < limit ? n : limit, g_descending ? "desc" : "asc");
    printf("%-12s  %s\n", "SIZE", "NAME");
    printf("%-12s  %s\n", "------------", "----");

    int show = n < limit ? n : limit;
    for (int i = 0; i < show; ++i) {
        char sz[32];
        format_size(entries[i].size, sz, sizeof(sz));
        printf("%-12s  %s\n", sz, entries[i].name);
    }

    free(entries);
    return 0;
}
