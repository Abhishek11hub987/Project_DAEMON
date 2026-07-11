/*
 * system_monitor.c — D.A.E.M.O.N. WSL2 System Profiler Daemon
 * ==============================================================
 *
 * A lightweight Linux C daemon that uses inotify to watch a project
 * directory for development events (GCC compilations, Valgrind logs)
 * and sends structured JSON reports to the Python FastAPI backend via
 * HTTP POST using libcurl.
 *
 * This design solves the WSL2 ↔ Windows filesystem boundary: instead
 * of writing directly to a shared file, the C daemon communicates over
 * the network loopback to the FastAPI server running on Windows.
 *
 * Monitored Events
 * ----------------
 *   - New/modified `.c` source files  → logs the save event
 *   - New `.o` or executable files    → infers GCC compilation success
 *   - New `valgrind-*.log` files      → parses memory leak summary
 *
 * Build
 * -----
 *   gcc -O2 -Wall -Wextra -o system_monitor system_monitor.c -lcurl
 *
 * Usage
 * -----
 *   ./system_monitor [watch_dir] [api_endpoint]
 *
 *   Defaults:
 *     watch_dir    = ~/dev
 *     api_endpoint = http://localhost:8000/api/agent_logs
 *
 * Dependencies
 * ------------
 *   - libcurl (apt install libcurl4-openssl-dev)
 *   - Linux kernel with inotify support (all modern Ubuntu)
 *
 * Author: D.A.E.M.O.N. Project — Cipher Agent Backend
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
#include <signal.h>
#include <sys/inotify.h>
#include <curl/curl.h>

/* ------------------------------------------------------------------ */
/* Configuration defaults                                              */
/* ------------------------------------------------------------------ */

#define DEFAULT_WATCH_DIR       "dev"   /* relative to $HOME */
#define DEFAULT_API_ENDPOINT    "http://localhost:8000/api/agent_logs"
#define EVENT_BUF_SIZE          (1024 * (sizeof(struct inotify_event) + 256))
#define JSON_BUF_SIZE           4096
#define VALGRIND_LINE_BUF       1024
#define MAX_SUMMARY_LEN         512

/* ------------------------------------------------------------------ */
/* Global state                                                        */
/* ------------------------------------------------------------------ */

static volatile sig_atomic_t g_running = 1;
static char g_watch_dir[512]    = {0};
static char g_api_endpoint[512] = {0};

/* ------------------------------------------------------------------ */
/* Signal handler for graceful shutdown                                 */
/* ------------------------------------------------------------------ */

static void signal_handler(int sig)
{
    (void)sig;
    g_running = 0;
    fprintf(stderr, "\n[system_monitor] Caught signal %d — shutting down.\n", sig);
}

/* ------------------------------------------------------------------ */
/* Timestamp helper (ISO 8601)                                         */
/* ------------------------------------------------------------------ */

static void get_iso_timestamp(char *buf, size_t len)
{
    time_t now = time(NULL);
    struct tm *t = gmtime(&now);
    strftime(buf, len, "%Y-%m-%dT%H:%M:%SZ", t);
}

/* ------------------------------------------------------------------ */
/* JSON escaping (minimal — handles quotes, backslashes, newlines)      */
/* ------------------------------------------------------------------ */

static void json_escape(const char *src, char *dst, size_t dst_len)
{
    size_t j = 0;
    for (size_t i = 0; src[i] && j < dst_len - 2; ++i) {
        switch (src[i]) {
            case '"':  dst[j++] = '\\'; dst[j++] = '"';  break;
            case '\\': dst[j++] = '\\'; dst[j++] = '\\'; break;
            case '\n': dst[j++] = '\\'; dst[j++] = 'n';  break;
            case '\r': dst[j++] = '\\'; dst[j++] = 'r';  break;
            case '\t': dst[j++] = '\\'; dst[j++] = 't';  break;
            default:   dst[j++] = src[i]; break;
        }
    }
    dst[j] = '\0';
}

/* ------------------------------------------------------------------ */
/* libcurl write callback (discard response body)                      */
/* ------------------------------------------------------------------ */

static size_t curl_discard_cb(void *data, size_t size, size_t nmemb, void *userp)
{
    (void)data; (void)userp;
    return size * nmemb;
}

/* ------------------------------------------------------------------ */
/* HTTP POST — send JSON payload to FastAPI                            */
/* ------------------------------------------------------------------ */

static int post_json(const char *json_payload)
{
    CURL *curl = curl_easy_init();
    if (!curl) {
        fprintf(stderr, "[system_monitor] curl_easy_init failed\n");
        return -1;
    }

    struct curl_slist *headers = NULL;
    headers = curl_slist_append(headers, "Content-Type: application/json");

    curl_easy_setopt(curl, CURLOPT_URL, g_api_endpoint);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_payload);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_discard_cb);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L);           /* 5s timeout */
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 3L);    /* 3s connect */

    CURLcode res = curl_easy_perform(curl);
    if (res != CURLE_OK) {
        fprintf(stderr,
                "[system_monitor] POST failed: %s (endpoint: %s)\n",
                curl_easy_strerror(res), g_api_endpoint);
    } else {
        long http_code = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
        if (http_code >= 200 && http_code < 300) {
            printf("[system_monitor] POST OK (%ld)\n", http_code);
        } else {
            fprintf(stderr,
                    "[system_monitor] POST HTTP %ld\n", http_code);
        }
    }

    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    return (res == CURLE_OK) ? 0 : -1;
}

/* ------------------------------------------------------------------ */
/* Build and send a generic event JSON                                 */
/* ------------------------------------------------------------------ */

static void send_event(const char *category, const char *summary,
                       const char *extra_json)
{
    char ts[64];
    get_iso_timestamp(ts, sizeof(ts));

    char esc_summary[MAX_SUMMARY_LEN * 2];
    json_escape(summary, esc_summary, sizeof(esc_summary));

    char json_buf[JSON_BUF_SIZE];
    int n = snprintf(json_buf, sizeof(json_buf),
        "{"
            "\"agent\": \"cipher\","
            "\"timestamp\": \"%s\","
            "\"category\": \"%s\","
            "\"summary\": \"%s\","
            "\"data\": {%s}"
        "}",
        ts, category, esc_summary,
        extra_json ? extra_json : ""
    );

    if (n < 0 || (size_t)n >= sizeof(json_buf)) {
        fprintf(stderr, "[system_monitor] JSON buffer overflow\n");
        return;
    }

    printf("[system_monitor] Event: [%s] %s\n", category, summary);
    post_json(json_buf);
}

/* ------------------------------------------------------------------ */
/* Valgrind log parser                                                 */
/* ------------------------------------------------------------------ */

typedef struct {
    long definitely_lost;
    long indirectly_lost;
    long possibly_lost;
    long still_reachable;
    long error_count;
    long allocs;
    long frees;
    int  parsed;              /* 1 if we found any Valgrind data */
} ValgrindReport;

static void parse_valgrind_log(const char *filepath, ValgrindReport *report)
{
    memset(report, 0, sizeof(*report));

    FILE *fp = fopen(filepath, "r");
    if (!fp) {
        fprintf(stderr,
                "[system_monitor] Cannot open Valgrind log: %s (%s)\n",
                filepath, strerror(errno));
        return;
    }

    char line[VALGRIND_LINE_BUF];
    while (fgets(line, sizeof(line), fp)) {
        /*
         * Valgrind LEAK SUMMARY lines look like:
         *   ==12345==    definitely lost: 72 bytes in 3 blocks
         *   ==12345==    indirectly lost: 0 bytes in 0 blocks
         *   ==12345==      possibly lost: 0 bytes in 0 blocks
         *   ==12345==    still reachable: 128 bytes in 1 blocks
         *
         * ERROR SUMMARY:
         *   ==12345== ERROR SUMMARY: 3 errors from 3 contexts
         *
         * HEAP SUMMARY:
         *   ==12345==   total heap usage: 10 allocs, 8 frees, ...
         */

        if (strstr(line, "definitely lost:")) {
            sscanf(strstr(line, "definitely lost:"),
                   "definitely lost: %ld", &report->definitely_lost);
            report->parsed = 1;
        }
        else if (strstr(line, "indirectly lost:")) {
            sscanf(strstr(line, "indirectly lost:"),
                   "indirectly lost: %ld", &report->indirectly_lost);
        }
        else if (strstr(line, "possibly lost:")) {
            sscanf(strstr(line, "possibly lost:"),
                   "possibly lost: %ld", &report->possibly_lost);
        }
        else if (strstr(line, "still reachable:")) {
            sscanf(strstr(line, "still reachable:"),
                   "still reachable: %ld", &report->still_reachable);
        }
        else if (strstr(line, "ERROR SUMMARY:")) {
            sscanf(strstr(line, "ERROR SUMMARY:"),
                   "ERROR SUMMARY: %ld", &report->error_count);
        }
        else if (strstr(line, "total heap usage:")) {
            sscanf(strstr(line, "total heap usage:"),
                   "total heap usage: %ld allocs, %ld frees",
                   &report->allocs, &report->frees);
        }
    }

    fclose(fp);
}

static void handle_valgrind_log(const char *filename)
{
    /* Build full path */
    char filepath[1024];
    snprintf(filepath, sizeof(filepath), "%s/%s", g_watch_dir, filename);

    /* Small delay to let Valgrind finish writing */
    usleep(500000);  /* 500ms */

    ValgrindReport report;
    parse_valgrind_log(filepath, &report);

    if (!report.parsed) {
        send_event("valgrind",
                   "Valgrind log detected but could not parse leak data.",
                   "\"parse_error\": true");
        return;
    }

    /* Build summary */
    char summary[MAX_SUMMARY_LEN];
    if (report.definitely_lost == 0 && report.error_count == 0) {
        snprintf(summary, sizeof(summary),
                 "Valgrind clean — no leaks, no errors. "
                 "%ld allocs, %ld frees.",
                 report.allocs, report.frees);
    } else {
        snprintf(summary, sizeof(summary),
                 "Valgrind found %ld bytes definitely lost, "
                 "%ld errors. %ld allocs, %ld frees.",
                 report.definitely_lost, report.error_count,
                 report.allocs, report.frees);
    }

    /* Build extra JSON data */
    char extra[1024];
    snprintf(extra, sizeof(extra),
        "\"definitely_lost_bytes\": %ld,"
        "\"indirectly_lost_bytes\": %ld,"
        "\"possibly_lost_bytes\": %ld,"
        "\"still_reachable_bytes\": %ld,"
        "\"error_count\": %ld,"
        "\"allocs\": %ld,"
        "\"frees\": %ld,"
        "\"log_file\": \"%s\"",
        report.definitely_lost,
        report.indirectly_lost,
        report.possibly_lost,
        report.still_reachable,
        report.error_count,
        report.allocs,
        report.frees,
        filename
    );

    send_event("valgrind", summary, extra);
}

/* ------------------------------------------------------------------ */
/* Event classification and dispatch                                   */
/* ------------------------------------------------------------------ */

static int has_suffix(const char *str, const char *suffix)
{
    size_t slen = strlen(str);
    size_t xlen = strlen(suffix);
    if (xlen > slen) return 0;
    return strcmp(str + slen - xlen, suffix) == 0;
}

static int has_prefix(const char *str, const char *prefix)
{
    return strncmp(str, prefix, strlen(prefix)) == 0;
}

static void handle_inotify_event(const struct inotify_event *event)
{
    if (!event->len || !event->name[0]) return;

    const char *name = event->name;

    /* Skip hidden files, editor swap files, temp files */
    if (name[0] == '.' || has_suffix(name, ".swp") ||
        has_suffix(name, ".tmp") || has_suffix(name, "~") ||
        has_suffix(name, ".part"))
    {
        return;
    }

    /* ── Valgrind log files ─────────────────────────────────── */
    if (has_prefix(name, "valgrind") && has_suffix(name, ".log")) {
        handle_valgrind_log(name);
        return;
    }

    /* ── C source file saved ────────────────────────────────── */
    if (has_suffix(name, ".c") || has_suffix(name, ".h")) {
        char summary[MAX_SUMMARY_LEN];
        snprintf(summary, sizeof(summary),
                 "Source file modified: %s", name);

        char extra[512];
        char esc_name[256];
        json_escape(name, esc_name, sizeof(esc_name));
        snprintf(extra, sizeof(extra),
                 "\"filename\": \"%s\", \"event\": \"source_modified\"",
                 esc_name);

        send_event("source_change", summary, extra);
        return;
    }

    /* ── Object files (.o) — likely GCC compilation ─────────── */
    if (has_suffix(name, ".o")) {
        char summary[MAX_SUMMARY_LEN];
        snprintf(summary, sizeof(summary),
                 "GCC compilation detected — object file created: %s", name);

        char extra[512];
        char esc_name[256];
        json_escape(name, esc_name, sizeof(esc_name));
        snprintf(extra, sizeof(extra),
                 "\"filename\": \"%s\", \"event\": \"compilation\"",
                 esc_name);

        send_event("gcc_compilation", summary, extra);
        return;
    }

    /*
     * ── New executable (no extension, executable bit set) ──────
     * Check if the file has the executable permission bit.
     */
    if (!strchr(name, '.')) {
        char filepath[1024];
        snprintf(filepath, sizeof(filepath), "%s/%s", g_watch_dir, name);

        if (access(filepath, X_OK) == 0) {
            char summary[MAX_SUMMARY_LEN];
            snprintf(summary, sizeof(summary),
                     "Executable binary created: %s (likely GCC link output)",
                     name);

            char extra[512];
            char esc_name[256];
            json_escape(name, esc_name, sizeof(esc_name));
            snprintf(extra, sizeof(extra),
                     "\"filename\": \"%s\", \"event\": \"link_output\"",
                     esc_name);

            send_event("gcc_compilation", summary, extra);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Main — inotify event loop                                           */
/* ------------------------------------------------------------------ */

static void print_usage(const char *prog)
{
    fprintf(stderr,
        "D.A.E.M.O.N. System Monitor — Cipher Agent Backend\n"
        "\n"
        "Usage: %s [watch_dir] [api_endpoint]\n"
        "\n"
        "Arguments:\n"
        "  watch_dir      Directory to monitor (default: ~/dev)\n"
        "  api_endpoint   FastAPI endpoint URL\n"
        "                 (default: %s)\n"
        "\n"
        "Build:\n"
        "  gcc -O2 -Wall -Wextra -o system_monitor system_monitor.c -lcurl\n"
        "\n"
        "Example:\n"
        "  %s ~/projects/my_c_app http://localhost:8000/api/agent_logs\n"
        "\n",
        prog, DEFAULT_API_ENDPOINT, prog);
}

int main(int argc, char **argv)
{
    /* ── Parse arguments ──────────────────────────────────────── */
    if (argc > 1 && (strcmp(argv[1], "-h") == 0 ||
                     strcmp(argv[1], "--help") == 0))
    {
        print_usage(argv[0]);
        return 0;
    }

    /* Watch directory */
    if (argc > 1) {
        strncpy(g_watch_dir, argv[1], sizeof(g_watch_dir) - 1);
    } else {
        const char *home = getenv("HOME");
        if (!home) home = "/tmp";
        snprintf(g_watch_dir, sizeof(g_watch_dir),
                 "%s/%s", home, DEFAULT_WATCH_DIR);
    }

    /* API endpoint */
    if (argc > 2) {
        strncpy(g_api_endpoint, argv[2], sizeof(g_api_endpoint) - 1);
    } else {
        strncpy(g_api_endpoint, DEFAULT_API_ENDPOINT,
                sizeof(g_api_endpoint) - 1);
    }

    /* ── Signal handlers ──────────────────────────────────────── */
    signal(SIGINT,  signal_handler);
    signal(SIGTERM, signal_handler);

    /* ── Startup banner ───────────────────────────────────────── */
    char ts[64];
    get_iso_timestamp(ts, sizeof(ts));
    printf("═══════════════════════════════════════════════════════\n");
    printf("  D.A.E.M.O.N. System Monitor — Cipher Agent Backend\n");
    printf("═══════════════════════════════════════════════════════\n");
    printf("  Watch dir  : %s\n", g_watch_dir);
    printf("  API target : %s\n", g_api_endpoint);
    printf("  Started at : %s\n", ts);
    printf("  PID        : %d\n", getpid());
    printf("═══════════════════════════════════════════════════════\n\n");

    /* ── Verify watch directory exists ─────────────────────────── */
    if (access(g_watch_dir, F_OK) != 0) {
        fprintf(stderr,
                "[system_monitor] Watch directory does not exist: %s\n"
                "  Create it with: mkdir -p %s\n",
                g_watch_dir, g_watch_dir);
        return 1;
    }

    /* ── Initialise libcurl ───────────────────────────────────── */
    curl_global_init(CURL_GLOBAL_DEFAULT);

    /* ── Initialise inotify ───────────────────────────────────── */
    int inotify_fd = inotify_init();
    if (inotify_fd < 0) {
        perror("[system_monitor] inotify_init");
        curl_global_cleanup();
        return 1;
    }

    /*
     * Watch for:
     *   IN_CREATE      — new files (executables, .o, valgrind logs)
     *   IN_CLOSE_WRITE — file finished being written (.c sources)
     *   IN_MOVED_TO    — file moved into the directory
     */
    int wd = inotify_add_watch(
        inotify_fd, g_watch_dir,
        IN_CREATE | IN_CLOSE_WRITE | IN_MOVED_TO
    );
    if (wd < 0) {
        fprintf(stderr,
                "[system_monitor] inotify_add_watch failed for '%s': %s\n",
                g_watch_dir, strerror(errno));
        close(inotify_fd);
        curl_global_cleanup();
        return 1;
    }

    /* Send a startup heartbeat event to the FastAPI server */
    send_event("heartbeat",
               "Cipher system monitor started — watching for build events.",
               "\"status\": \"online\"");

    printf("[system_monitor] Watching '%s' for events...\n\n", g_watch_dir);

    /* ── Main event loop ──────────────────────────────────────── */
    char event_buf[EVENT_BUF_SIZE] __attribute__((aligned(8)));

    while (g_running) {
        ssize_t len = read(inotify_fd, event_buf, sizeof(event_buf));

        if (len < 0) {
            if (errno == EINTR) {
                /* Interrupted by signal — check g_running and loop */
                continue;
            }
            perror("[system_monitor] inotify read");
            break;
        }

        /* Process all events in the buffer */
        for (char *ptr = event_buf; ptr < event_buf + len; ) {
            struct inotify_event *event = (struct inotify_event *)ptr;
            handle_inotify_event(event);
            ptr += sizeof(struct inotify_event) + event->len;
        }
    }

    /* ── Cleanup ──────────────────────────────────────────────── */
    printf("\n[system_monitor] Cleaning up...\n");

    /* Send a shutdown event */
    send_event("heartbeat",
               "Cipher system monitor shutting down.",
               "\"status\": \"offline\"");

    inotify_rm_watch(inotify_fd, wd);
    close(inotify_fd);
    curl_global_cleanup();

    printf("[system_monitor] Goodbye.\n");
    return 0;
}
