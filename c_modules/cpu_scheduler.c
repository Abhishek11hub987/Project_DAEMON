/*
 * cpu_scheduler.c - CPU scheduling visualizer for D.A.E.M.O.N.
 *
 * Implements two algorithms with an ASCII Gantt chart:
 *   - FCFS (First-Come First-Served)
 *   - Round Robin (configurable quantum)
 *
 * Usage:
 *   cpu_scheduler <algorithm> [quantum] < input.txt
 *   cpu_scheduler fcfs
 *   cpu_scheduler rr 2
 *
 * If no stdin is provided, a built-in demo workload is used so the program
 * can be invoked directly from D.A.E.M.O.N. by voice.
 *
 * Input format (one process per line):
 *   <pid> <arrival_time> <burst_time>
 *
 * Build:
 *   gcc -O2 -Wall -o cpu_scheduler cpu_scheduler.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_PROCS 64
#define MAX_TIMELINE 4096

typedef struct {
    int pid;
    int arrival;
    int burst;
    int remaining;
    int completion;
    int waiting;
    int turnaround;
    int started;       /* boolean: has process started running */
} Process;

static int load_demo(Process *p) {
    /* Classic textbook example */
    Process demo[] = {
        {1, 0, 6, 6, 0, 0, 0, 0},
        {2, 1, 4, 4, 0, 0, 0, 0},
        {3, 2, 2, 2, 0, 0, 0, 0},
        {4, 3, 3, 3, 0, 0, 0, 0},
    };
    int n = sizeof(demo) / sizeof(demo[0]);
    memcpy(p, demo, sizeof(demo));
    return n;
}

static int load_stdin(Process *p) {
    int n = 0;
    while (n < MAX_PROCS &&
           scanf("%d %d %d", &p[n].pid, &p[n].arrival, &p[n].burst) == 3) {
        p[n].remaining = p[n].burst;
        p[n].completion = 0;
        p[n].waiting = 0;
        p[n].turnaround = 0;
        p[n].started = 0;
        ++n;
    }
    return n;
}

static void print_gantt(const int *timeline, int len) {
    printf("\nGantt chart:\n ");
    for (int i = 0; i < len; ++i) printf("----");
    printf("\n|");
    for (int i = 0; i < len; ++i) {
        if (timeline[i] < 0) printf(" .. |");
        else                 printf(" P%-2d|", timeline[i]);
    }
    printf("\n ");
    for (int i = 0; i < len; ++i) printf("----");
    printf("\n0");
    for (int i = 0; i < len; ++i) printf("%4d", i + 1);
    printf("\n");
}

static void print_stats(Process *p, int n) {
    double avg_wait = 0, avg_tat = 0;
    printf("\nPID  Arrival  Burst  Completion  Waiting  Turnaround\n");
    printf("---  -------  -----  ----------  -------  ----------\n");
    for (int i = 0; i < n; ++i) {
        printf("%-3d  %-7d  %-5d  %-10d  %-7d  %-10d\n",
               p[i].pid, p[i].arrival, p[i].burst,
               p[i].completion, p[i].waiting, p[i].turnaround);
        avg_wait += p[i].waiting;
        avg_tat  += p[i].turnaround;
    }
    printf("\nAverage waiting time    : %.2f\n", avg_wait / n);
    printf("Average turnaround time : %.2f\n", avg_tat / n);
}

static int cmp_arrival(const void *a, const void *b) {
    const Process *pa = (const Process *)a;
    const Process *pb = (const Process *)b;
    if (pa->arrival != pb->arrival) return pa->arrival - pb->arrival;
    return pa->pid - pb->pid;
}

static void run_fcfs(Process *p, int n) {
    qsort(p, n, sizeof(Process), cmp_arrival);
    int t = 0;
    int timeline[MAX_TIMELINE];
    int tl = 0;

    for (int i = 0; i < n; ++i) {
        if (t < p[i].arrival) {
            while (t < p[i].arrival && tl < MAX_TIMELINE) {
                timeline[tl++] = -1; /* idle */
                ++t;
            }
        }
        for (int b = 0; b < p[i].burst && tl < MAX_TIMELINE; ++b) {
            timeline[tl++] = p[i].pid;
            ++t;
        }
        p[i].completion = t;
        p[i].turnaround = p[i].completion - p[i].arrival;
        p[i].waiting    = p[i].turnaround - p[i].burst;
    }
    printf("Algorithm: FCFS  (n=%d)\n", n);
    print_gantt(timeline, tl);
    print_stats(p, n);
}

static void run_rr(Process *p, int n, int quantum) {
    qsort(p, n, sizeof(Process), cmp_arrival);

    int queue[MAX_PROCS * 16];
    int qh = 0, qt = 0;
    int t = 0;
    int done = 0;
    int next_arrival = 0;
    int timeline[MAX_TIMELINE];
    int tl = 0;

    /* Enqueue everything that has arrived by t = 0 */
    while (next_arrival < n && p[next_arrival].arrival <= t) {
        queue[qt++] = next_arrival++;
    }

    while (done < n && tl < MAX_TIMELINE) {
        if (qh == qt) {
            timeline[tl++] = -1;
            ++t;
            while (next_arrival < n && p[next_arrival].arrival <= t) {
                queue[qt++] = next_arrival++;
            }
            continue;
        }
        int idx = queue[qh++];
        int slice = p[idx].remaining < quantum ? p[idx].remaining : quantum;
        for (int s = 0; s < slice && tl < MAX_TIMELINE; ++s) {
            timeline[tl++] = p[idx].pid;
            ++t;
            while (next_arrival < n && p[next_arrival].arrival <= t) {
                queue[qt++] = next_arrival++;
            }
        }
        p[idx].remaining -= slice;
        if (p[idx].remaining == 0) {
            p[idx].completion = t;
            p[idx].turnaround = p[idx].completion - p[idx].arrival;
            p[idx].waiting    = p[idx].turnaround - p[idx].burst;
            ++done;
        } else {
            queue[qt++] = idx; /* requeue */
        }
    }

    printf("Algorithm: Round Robin  (n=%d, quantum=%d)\n", n, quantum);
    print_gantt(timeline, tl);
    print_stats(p, n);
}

int main(int argc, char **argv) {
    const char *algo = (argc > 1) ? argv[1] : "fcfs";
    int quantum = (argc > 2) ? atoi(argv[2]) : 2;
    if (quantum < 1) quantum = 2;

    Process procs[MAX_PROCS];
    int n;

    /* Detect whether stdin has data; otherwise use demo workload. */
    int c = getc(stdin);
    if (c == EOF) {
        n = load_demo(procs);
        printf("(no stdin) using built-in demo workload of %d processes\n", n);
    } else {
        ungetc(c, stdin);
        n = load_stdin(procs);
        if (n == 0) {
            n = load_demo(procs);
            printf("(empty input) using built-in demo workload\n");
        }
    }

    if (strcmp(algo, "fcfs") == 0) {
        run_fcfs(procs, n);
    } else if (strcmp(algo, "rr") == 0) {
        run_rr(procs, n, quantum);
    } else {
        fprintf(stderr, "Unknown algorithm '%s'. Use 'fcfs' or 'rr'.\n", algo);
        return 1;
    }
    return 0;
}
